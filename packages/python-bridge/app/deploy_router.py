#!/usr/bin/env python3
"""
deploy_router.py - Centralized project deployment for contabo_storage_manager

Allows per-project deploy scripts (in other repos) to upload builds to a remote
target (storage.1ink.us etc.) WITHOUT embedding any FTP/SFTP credentials.

Endpoints (token auth via X-Deploy-Token when DEPLOY_AUTH_TOKEN is set):
  POST /api/deploy/{project_name}/file   — single file + rel_path [+ target_folder]
  POST /api/deploy/{project_name}/zip    — zip of build tree [+ target_folder]

All DEPLOY_* settings live only in the VPS .env. StorageFTPClient is called
with explicit overrides so the internal FTP vs deploy target can differ.
"""

import io
import zipfile
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Header
from typing import Optional
from pathlib import Path

from .config import settings
from .ftp_client import StorageFTPClient
from .logger import get_logger

logger = get_logger("deploy_router")

router = APIRouter(prefix="/api/deploy", tags=["deploy"])

_deploy_client: Optional[StorageFTPClient] = None


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
    # Split, sanitize each segment, rejoin
    try:
        parts = [_sanitize_path(p) for p in Path(raw).parts if p not in (".", "")]
        if not parts:
            return None
        return str(Path(*parts))
    except HTTPException:
        raise


@router.post("/{project_name}/file")
async def upload_project_file(
    project_name: str,
    file: UploadFile = File(...),
    rel_path: str = Form(..., description="Relative path inside the project, e.g. 'js/app.js' or 'index.html'"),
    target_folder: Optional[str] = Form(
        default=None,
        description="Optional target subfolder (supports nested paths like 'my-site' or 'sites/example.com'). Defaults to project_name.",
    ),
    x_deploy_token: Optional[str] = Header(default=None, alias="X-Deploy-Token"),
):
    """Upload a single file for a project deployment.

    The file lands at: {deploy_base}/{target_prefix}/{rel_path}
    where target_prefix = sanitized(target_folder) or sanitized(project_name)
    """
    _check_token(x_deploy_token, project_name)
    project_name = _sanitize_path(project_name)
    target_prefix = _sanitize_target_folder(target_folder) or project_name

    # Support rel_path with subdirectories (each segment sanitized)
    rel_parts = [_sanitize_path(p) for p in Path(rel_path).parts if p not in (".", "")]
    safe_rel_path = str(Path(*rel_parts)) if rel_parts else ""

    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    try:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Empty file upload")

        client = get_deploy_client()
        target_rel = f"{target_prefix}/{safe_rel_path}" if safe_rel_path else target_prefix

        logger.info(
            "DEPLOY file: project=%s target_prefix=%s rel_path=%s size=%d bytes",
            project_name, target_prefix, safe_rel_path or "(root)", len(content)
        )

        client.upload_bytes(content, target_rel)

        remote_target = f"{settings.deploy_base_dir or ''}/{target_rel}".replace("//", "/")
        logger.info("DEPLOY file OK: %s (%d bytes) -> %s", target_rel, len(content), remote_target)

        return {
            "status": "success",
            "project": project_name,
            "target_prefix": target_prefix,
            "rel_path": safe_rel_path,
            "size": len(content),
            "target": remote_target,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("DEPLOY file error for %s/%s: %s", project_name, safe_rel_path, str(e))
        raise HTTPException(status_code=500, detail=f"Deploy failed: {e}")


@router.post("/{project_name}/zip")
async def upload_project_zip(
    project_name: str,
    archive: UploadFile = File(..., description="Zip archive of the build output (zip the *contents* of dist/build, not a wrapper dir)"),
    target_folder: Optional[str] = Form(
        default=None,
        description="Optional target subfolder (supports nested paths). Defaults to project_name.",
    ),
    x_deploy_token: Optional[str] = Header(default=None, alias="X-Deploy-Token"),
):
    """
    Upload a zip archive of a project build in a single request (efficient).

    The server extracts in-memory (zipfile + BytesIO) and uploads every file
    over the (persistent) StorageFTPClient connection using DEPLOY_* credentials.

    Recommended client usage:
      - Zip the *contents* of your dist/ or build/ folder (so index.html is at root of zip)
      - POST the .zip using multipart field name 'archive'
      - Optionally pass target_folder (form field) to deploy under a custom subdir on the remote

    Final remote location: {DEPLOY_BASE_DIR}/{target_prefix}/{zip-entry-path}
    """
    _check_token(x_deploy_token, project_name)
    project_name = _sanitize_path(project_name)
    target_prefix = _sanitize_target_folder(target_folder) or project_name

    try:
        raw = await archive.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read zip: {e}")

    zip_size = len(raw)
    if zip_size == 0:
        raise HTTPException(status_code=400, detail="Empty zip archive")

    logger.info(
        "DEPLOY zip: project=%s target_prefix=%s zip_size=%d bytes",
        project_name, target_prefix, zip_size
    )

    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as e:
        raise HTTPException(status_code=400, detail=f"Invalid zip file: {e}")

    client = get_deploy_client()
    uploaded = []
    failed = []
    total_bytes = 0

    for zip_entry in zf.infolist():
        if zip_entry.is_dir():
            continue

        # Sanitize each path component individually (supports subdirs inside zip)
        parts = Path(zip_entry.filename).parts
        try:
            safe_parts = [_sanitize_path(p) for p in parts if p not in (".", "")]
            if not safe_parts:
                continue
        except HTTPException:
            logger.warning("DEPLOY zip: skipping unsafe entry project=%s entry=%s", project_name, zip_entry.filename)
            failed.append({"path": zip_entry.filename, "error": "unsafe path (traversal or invalid chars)"})
            continue

        safe_rel = str(Path(*safe_parts))
        target_rel = f"{target_prefix}/{safe_rel}"

        try:
            data = zf.read(zip_entry.filename)
            data_len = len(data)
            total_bytes += data_len
            client.upload_bytes(data, target_rel)
            uploaded.append(safe_rel)
            logger.debug("DEPLOY zip entry OK: %s (%d bytes)", target_rel, data_len)
        except Exception as e:
            logger.error("DEPLOY zip entry failed: %s (%s)", target_rel, e)
            failed.append({"path": safe_rel, "error": str(e)})

    if failed and not uploaded:
        logger.error("DEPLOY zip: ALL files failed for project=%s (%d failed)", project_name, len(failed))
        raise HTTPException(status_code=500, detail={"message": "All files failed to upload", "failed": failed})

    remote_base = (settings.deploy_base_dir or "").rstrip("/")
    logger.info(
        "DEPLOY zip COMPLETE: project=%s target_prefix=%s uploaded=%d failed=%d total_bytes=%d -> %s/%s",
        project_name, target_prefix, len(uploaded), len(failed), total_bytes, remote_base, target_prefix
    )

    return {
        "status": "success" if not failed else "partial",
        "project": project_name,
        "target_prefix": target_prefix,
        "uploaded": len(uploaded),
        "failed": failed,
        "files": uploaded,
        "total_bytes": total_bytes,
        "zip_size": zip_size,
    }


@router.get("/health")
async def deploy_health():
    """Health and configuration status for the deploy system."""
    configured = bool(settings.deploy_host and settings.deploy_user)
    return {
        "status": "ok" if configured else "not_configured",
        "deploy_host": settings.deploy_host,
        "deploy_port": settings.deploy_port,
        "base_dir": settings.deploy_base_dir,
        "has_token": bool(settings.deploy_auth_token),
        "endpoints": {
            "file": "POST /api/deploy/{project}/file  (multipart: file, rel_path, optional target_folder)",
            "zip": "POST /api/deploy/{project}/zip   (multipart: archive, optional target_folder) — recommended for speed",
        },
        "message": "Centralized deploys. Credentials live only on the VPS. Use X-Deploy-Token header when DEPLOY_AUTH_TOKEN is set.",
    }
