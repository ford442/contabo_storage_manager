#!/usr/bin/env python3
"""
deploy_router.py - Centralized project deployment for contabo_storage_manager

Allows per-project deploy scripts (in other repos) to upload builds to a remote
target (storage.1ink.us etc.) WITHOUT embedding any FTP/SFTP credentials.

Endpoints (token auth via X-Deploy-Token when DEPLOY_AUTH_TOKEN is set):
  POST /api/deploy/{project_name}/file   — single file + rel_path [+ target_folder]
  POST /api/deploy/{project_name}/zip    — zip of build tree [+ target_folder]
  POST /api/deploy/{project_name}/bundle — legacy alias for /zip (for older clients)

All DEPLOY_* settings live only in the VPS .env. StorageFTPClient is called
with explicit overrides so the internal FTP vs deploy target can differ.
"""

import io
import zipfile
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Header, Query
from typing import Optional, Literal
from pathlib import Path

from .config import settings
from .ftp_client import StorageFTPClient
from .logger import get_logger

logger = get_logger("deploy_router")

router = APIRouter(prefix="/api/deploy", tags=["deploy"])

# Outer retry layer on top of ftp_client's per-upload reconnect logic.
UPLOAD_MAX_ATTEMPTS = 3


def _upload_bytes_with_retries(client: StorageFTPClient, data: bytes, target_rel: str) -> None:
    """Upload with retries; resets the client connection between attempts."""
    last_err: Optional[Exception] = None
    for attempt in range(UPLOAD_MAX_ATTEMPTS):
        try:
            client.upload_bytes(data, target_rel)
            return
        except Exception as e:
            last_err = e
            if attempt < UPLOAD_MAX_ATTEMPTS - 1:
                logger.warning(
                    "DEPLOY upload retry %d/%d for %s: %s",
                    attempt + 1,
                    UPLOAD_MAX_ATTEMPTS,
                    target_rel,
                    e,
                )
                client.close()
    if last_err is not None:
        raise last_err

_deploy_client: Optional[StorageFTPClient] = None
_deploy_client_go: Optional[StorageFTPClient] = None
_deploy_client_prod: Optional[StorageFTPClient] = None


def get_deploy_client() -> StorageFTPClient:
    """Return the shared (persistent-connection) deploy client."""
    global _deploy_client
    if _deploy_client is None:
        if not settings.deploy_host:
            logger.warning("DEPLOY_HOST not configured - deploys will fail")
        _deploy_client = StorageFTPClient(
            host=settings.deploy_host,
            user=settings.deploy_user,
            password=settings.deploy_pass,
            port=settings.deploy_port,
            base_dir=settings.deploy_base_dir,
        )
    return _deploy_client


def get_deploy_client_go() -> StorageFTPClient:
    """Return the shared (persistent-connection) deploy client for go target."""
    global _deploy_client_go
    if not settings.deploy_base_dir_go:
        raise HTTPException(status_code=400, detail="DEPLOY_BASE_DIR_GO is not configured")
    if _deploy_client_go is None:
        if not settings.deploy_host:
            logger.warning("DEPLOY_HOST not configured - deploys will fail")
        _deploy_client_go = StorageFTPClient(
            host=settings.deploy_host,
            user=settings.deploy_user,
            password=settings.deploy_pass,
            port=settings.deploy_port,
            base_dir=settings.deploy_base_dir_go,
        )
    return _deploy_client_go


def get_deploy_client_prod() -> StorageFTPClient:
    """Return the shared deploy client for production (projectm.1ink.us)."""
    global _deploy_client_prod
    if not settings.deploy_base_dir_prod:
        raise HTTPException(status_code=400, detail="DEPLOY_BASE_DIR_PROD is not configured")
    if _deploy_client_prod is None:
        if not settings.deploy_host:
            logger.warning("DEPLOY_HOST not configured - deploys will fail")
        _deploy_client_prod = StorageFTPClient(
            host=settings.deploy_host,
            user=settings.deploy_user,
            password=settings.deploy_pass,
            port=settings.deploy_port,
            base_dir=settings.deploy_base_dir_prod,
        )
    return _deploy_client_prod


def _resolve_target_site(*vals) -> str:
    """Normalize target_site / DEPLOY_TARGET / target form fields to test|go|prod."""
    for v in vals:
        if isinstance(v, str) and v.strip():
            name = v.strip().lower()
            if name in ("test", "go", "prod"):
                return name
    return "test"


def _client_and_base_for_target(effective_target: str):
    if effective_target == "go":
        return get_deploy_client_go(), settings.deploy_base_dir_go
    if effective_target == "prod":
        return get_deploy_client_prod(), settings.deploy_base_dir_prod
    return get_deploy_client(), settings.deploy_base_dir


def _check_token(x_deploy_token: Optional[str], project_name: str):
    if settings.deploy_auth_token:
        if not x_deploy_token or x_deploy_token != settings.deploy_auth_token:
            logger.warning("Deploy attempt with invalid/missing token for project=%s", project_name)
            raise HTTPException(status_code=403, detail="Invalid or missing X-Deploy-Token")


def _sanitize_path(name: str) -> str:
    """Sanitize a single path component. Rejects traversal and unsafe chars."""
    if not name or ".." in name or name.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid path component")
    cleaned = "".join(c for c in name if c.isalnum() or c in "-_.")
    if not cleaned:
        raise HTTPException(status_code=400, detail="Invalid project or path name")
    return cleaned


def _sanitize_target_folder(raw: Optional[str]) -> Optional[str]:
    """Sanitize an optional target_folder that may contain subdirectories (e.g. 'sites/foo' or 'v1')."""
    if not raw:
        return None
    raw = raw.strip().strip("/")
    if not raw:
        return None
    try:
        parts = [_sanitize_path(p) for p in Path(raw).parts if p not in (".", "")]
        if not parts:
            return None
        return str(Path(*parts))
    except HTTPException:
        raise


def _remote_size_map(client: StorageFTPClient, target_prefix: str) -> dict[str, int]:
    """Best-effort remote size index. Missing dirs / listing errors → empty map (upload all)."""
    try:
        return client.list_file_sizes(target_prefix) or {}
    except Exception as exc:
        logger.warning("DEPLOY size listing failed for %s: %s", target_prefix, exc)
        return {}


@router.post("/{project_name}/file")
async def upload_project_file(
    project_name: str,
    file: UploadFile = File(...),
    rel_path: str = Form(..., description="Relative path inside the project, e.g. 'js/app.js' or 'index.html'"),
    target_folder: Optional[str] = Form(
        default=None,
        description="Optional target subfolder (supports nested paths like 'my-site' or 'sites/example.com'). Defaults to project_name.",
    ),
    target_site: Literal["test", "go", "prod"] = Form(default="test", description="Deploy target site: 'test' (default), 'go', or 'prod'."),
    target: Optional[str] = Form(default=None, description="Alternative name for target_site (compat)."),
    deploy_target: Optional[str] = Form(default=None, description="Alternative name for target_site, e.g. DEPLOY_TARGET env var in client scripts."),
    x_deploy_token: Optional[str] = Header(default=None, alias="X-Deploy-Token"),
):
    """Upload a single file for a project deployment.

    The file lands at: {deploy_base}/{target_prefix}/{rel_path}
    where target_prefix = sanitized(target_folder) or sanitized(project_name)
    """
    _check_token(x_deploy_token, project_name)
    project_name = _sanitize_path(project_name)
    target_prefix = _sanitize_target_folder(target_folder) or project_name

    rel_parts = [_sanitize_path(p) for p in Path(rel_path).parts if p not in (".", "")]
    safe_rel_path = str(Path(*rel_parts)) if rel_parts else ""

    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    # Support DEPLOY_TARGET (common client var name), target, and target_site.
    effective_target = _resolve_target_site(target_site, target, deploy_target)

    try:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Empty file upload")

        client, base_dir = _client_and_base_for_target(effective_target)
        target_rel = f"{target_prefix}/{safe_rel_path}" if safe_rel_path else target_prefix
        content_len = len(content)

        logger.info(
            "DEPLOY file: project=%s target=%s target_prefix=%s rel_path=%s size=%d bytes",
            project_name, effective_target, target_prefix, safe_rel_path or "(root)", content_len
        )

        skipped = False
        try:
            remote_size = client.remote_file_size(target_rel)
        except Exception:
            remote_size = None
        if remote_size is not None and remote_size == content_len:
            skipped = True
            logger.info("DEPLOY file SKIP same-size: %s (%d bytes)", target_rel, content_len)
        else:
            _upload_bytes_with_retries(client, content, target_rel)

        remote_target = f"{base_dir or ''}/{target_rel}".replace("//", "/")
        logger.info(
            "DEPLOY file %s: %s (%d bytes) -> %s",
            "SKIP" if skipped else "OK",
            target_rel,
            content_len,
            remote_target,
        )

        return {
            "status": "success",
            "project": project_name,
            "target_site": effective_target,
            "target_prefix": target_prefix,
            "rel_path": safe_rel_path,
            "size": content_len,
            "skipped": skipped,
            "target": remote_target,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("DEPLOY file error for %s/%s: %s", project_name, safe_rel_path, str(e))
        raise HTTPException(status_code=500, detail=f"Deploy failed: {e}")


@router.post("/{project_name}/zip")
@router.post("/{project_name}/bundle", include_in_schema=False)  # legacy alias for older clients ("Uploading bundle...")
async def upload_project_zip(
    project_name: str,
    # Support multiple field names for maximum backward compatibility with old deploy scripts
    archive: UploadFile = File(None, description="Zip archive (preferred field name)"),
    bundle: UploadFile = File(None, description="Legacy field name from early skeletons"),
    file: UploadFile = File(None, description="Generic file field (some clients)"),
    zipfile_upload: UploadFile = File(None, description="Legacy field name"),
    target_folder: Optional[str] = Form(
        default=None,
        description="Optional target subfolder (supports nested paths). Defaults to project_name.",
    ),
    target_site: Literal["test", "go", "prod"] = Form(default="test", description="Deploy target site: 'test' (default), 'go', or 'prod'."),
    target: Optional[str] = Form(default=None, description="Alternative name for target_site (compat)."),
    deploy_target: Optional[str] = Form(default=None, description="Alternative name for target_site, e.g. DEPLOY_TARGET env var in client scripts."),
    force: Optional[str] = Form(
        default=None,
        description="If '1'/'true'/'yes', upload every zip entry even when remote size matches.",
    ),
    force_q: Optional[str] = Query(
        default=None,
        alias="force_upload",
        description="Query-string override for force upload (same values as form force).",
    ),
    x_deploy_token: Optional[str] = Header(default=None, alias="X-Deploy-Token"),
):
    """
    Upload a zip archive of a project build in a single request (efficient).

    The server extracts in-memory (zipfile + BytesIO) and uploads every file
    over the (persistent) StorageFTPClient connection using DEPLOY_* credentials.

    Both /zip (canonical per spec) and /bundle (legacy) are supported so older
    deploy scripts continue to work without changes.

    Recommended client usage:
      - Zip the *contents* of your dist/ or build/ folder
      - POST using multipart field 'archive' (or 'bundle' / 'file' for old scripts)
      - Optionally pass target_folder + target_site (or DEPLOY_TARGET / target)
    """
    _check_token(x_deploy_token, project_name)
    project_name = _sanitize_path(project_name)
    target_prefix = _sanitize_target_folder(target_folder) or project_name

    # Pick whichever field the client actually sent
    upload = archive or bundle or file or zipfile_upload
    if upload is None or not upload.filename:
        raise HTTPException(status_code=400, detail="No zip archive provided. Use field 'archive', 'bundle', or 'file'.")

    effective_target = _resolve_target_site(target_site, target, deploy_target)

    try:
        raw = await upload.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read zip: {e}")

    zip_size = len(raw)
    if zip_size == 0:
        raise HTTPException(status_code=400, detail="Empty zip archive")

    logger.info(
        "DEPLOY zip: project=%s target_prefix=%s zip_size=%d bytes target=%s (legacy /bundle path accepted if used)",
        project_name, target_prefix, zip_size, effective_target
    )

    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as e:
        raise HTTPException(status_code=400, detail=f"Invalid zip file: {e}")

    client, remote_base = _client_and_base_for_target(effective_target)
    force_raw = force or force_q or ""
    force_upload = force_raw.strip().lower() in ("1", "true", "yes")
    logger.info("DEPLOY zip force_upload=%s (form=%r query=%r)", force_upload, force, force_q)
    remote_sizes = {} if force_upload else _remote_size_map(client, target_prefix)
    uploaded = []
    skipped = []
    failed = []
    total_bytes = 0

    for zip_entry in zf.infolist():
        if zip_entry.is_dir():
            continue

        parts = Path(zip_entry.filename).parts
        try:
            safe_parts = [_sanitize_path(p) for p in parts if p not in (".", "")]
            if not safe_parts:
                continue
        except HTTPException:
            logger.warning("DEPLOY zip: skipping unsafe entry project=%s entry=%s", project_name, zip_entry.filename)
            failed.append({"path": zip_entry.filename, "error": "unsafe path (traversal or invalid chars)"})
            continue

        safe_rel = str(Path(*safe_parts)).replace("\\", "/")
        target_rel = f"{target_prefix}/{safe_rel}"

        try:
            data = zf.read(zip_entry.filename)
            data_len = len(data)
            total_bytes += data_len
            if not force_upload and remote_sizes.get(safe_rel) == data_len:
                skipped.append(safe_rel)
                logger.debug("DEPLOY zip entry SKIP same-size: %s (%d bytes)", target_rel, data_len)
                continue
            _upload_bytes_with_retries(client, data, target_rel)
            uploaded.append(safe_rel)
            logger.debug("DEPLOY zip entry OK: %s (%d bytes)", target_rel, data_len)
        except Exception as e:
            logger.error("DEPLOY zip entry failed: %s (%s)", target_rel, e)
            failed.append({"path": safe_rel, "error": str(e)})

    if failed and not uploaded and not skipped:
        logger.error("DEPLOY zip: ALL files failed for project=%s (%d failed)", project_name, len(failed))
        raise HTTPException(status_code=500, detail={"message": "All files failed to upload", "failed": failed})

    remote_base = (remote_base or "").rstrip("/")
    logger.info(
        "DEPLOY zip COMPLETE: project=%s target_site=%s target_prefix=%s uploaded=%d skipped=%d failed=%d total_bytes=%d -> %s/%s",
        project_name, effective_target, target_prefix, len(uploaded), len(skipped), len(failed), total_bytes, remote_base, target_prefix
    )

    return {
        "status": "success" if not failed else "partial",
        "project": project_name,
        "target_site": effective_target,
        "target_prefix": target_prefix,
        "uploaded": len(uploaded),
        "skipped": len(skipped),
        "failed": failed,
        "files": uploaded,
        "skipped_files": skipped,
        "total_bytes": total_bytes,
        "zip_size": zip_size,
    }


@router.get("/{project_name}/sizes")
async def list_project_remote_sizes(
    project_name: str,
    target_folder: Optional[str] = None,
    target_site: Literal["test", "go", "prod"] = "test",
    target: Optional[str] = None,
    deploy_target: Optional[str] = None,
    x_deploy_token: Optional[str] = Header(default=None, alias="X-Deploy-Token"),
):
    """Return {relative_path: size} for files already on the deploy target.

    Clients use this to omit unchanged (same-byte-size) files from the zip.
    Old deploy.py scripts that skip this call still work; the zip handler also
    skips same-size files on the VPS→FTP hop.
    """
    _check_token(x_deploy_token, project_name)
    project_name = _sanitize_path(project_name)
    target_prefix = _sanitize_target_folder(target_folder) or project_name
    effective_target = _resolve_target_site(target_site, target, deploy_target)

    client, remote_base = _client_and_base_for_target(effective_target)
    files = _remote_size_map(client, target_prefix)
    return {
        "status": "ok",
        "project": project_name,
        "target_site": effective_target,
        "target_prefix": target_prefix,
        "count": len(files),
        "files": files,
        "base": (remote_base or "").rstrip("/"),
    }


@router.get("/health")
async def deploy_health():
    """Health and configuration status for the centralized deploy system."""
    configured = bool(settings.deploy_host and settings.deploy_user)
    return {
        "status": "ok" if configured else "not_configured",
        "deploy_host": settings.deploy_host,
        "deploy_port": settings.deploy_port,
        "base_dir": settings.deploy_base_dir,
        "deploy_base_dir_go": settings.deploy_base_dir_go,
        "deploy_base_dir_prod": settings.deploy_base_dir_prod,
        "has_token": bool(settings.deploy_auth_token),
        "endpoints": {
            "file": "POST /api/deploy/{project}/file  (file + rel_path + optional target_folder + optional target_site=test|go|prod)",
            "zip": "POST /api/deploy/{project}/zip   (archive + optional target_folder + optional target_site=test|go|prod) — recommended",
            "bundle": "POST /api/deploy/{project}/bundle (legacy alias for /zip — still works for old clients)",
            "sizes": "GET /api/deploy/{project}/sizes?target_site=test|go|prod&target_folder=...  — remote {path: bytes} map for client-side zip skip",
        },
        "message": "Centralized deploys. Credentials live only on the VPS. Both /zip and legacy /bundle paths are supported. Use optional target_site=test|go|prod (default test) or send field 'DEPLOY_TARGET' / 'target' / 'target_site'. Requires DEPLOY_BASE_DIR_GO for 'go' and DEPLOY_BASE_DIR_PROD for 'prod' (DreamHost projectm.1ink.us parent, e.g. /home/ford442). X-Deploy-Token header when DEPLOY_AUTH_TOKEN is set.",
    }
