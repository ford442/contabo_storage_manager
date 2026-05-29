#!/usr/bin/env python3
"""
deploy_router.py - Project deployment endpoint for contabo_storage_manager

Allows per-project deploy.py scripts (in other repos) to upload built files
to https://storage.noahcohn.com/api/deploy/{project}/file   (single file)
  or https://storage.noahcohn.com/api/deploy/{project}/bundle (zip archive)

The server uses DEPLOY_* credentials (stored only in .env on the VPS) to push
files to the target (e.g. storage.1ink.us) via SFTP/FTPS — no passwords needed
in individual project repos.
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
    """Prevent path traversal."""
    if not name or ".." in name or name.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid path component")
    cleaned = "".join(c for c in name if c.isalnum() or c in "-_.")
    if not cleaned:
        raise HTTPException(status_code=400, detail="Invalid project or path name")
    return cleaned


@router.post("/{project_name}/file")
async def upload_project_file(
    project_name: str,
    file: UploadFile = File(...),
    rel_path: str = Form(..., description="Relative path inside the project, e.g. 'js/app.js'"),
    x_deploy_token: Optional[str] = Header(default=None, alias="X-Deploy-Token"),
):
    """Upload a single file for a project deployment."""
    _check_token(x_deploy_token, project_name)

    project_name = _sanitize_path(project_name)
    rel_parts = [_sanitize_path(p) for p in Path(rel_path).parts]
    safe_rel_path = str(Path(*rel_parts))

    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    try:
        content = await file.read()
        client = get_deploy_client()
        target_rel = f"{project_name}/{safe_rel_path}"
        client.upload_bytes(content, target_rel)

        logger.info("Deploy upload OK: project=%s path=%s size=%d", project_name, safe_rel_path, len(content))
        return {
            "status": "success",
            "project": project_name,
            "rel_path": safe_rel_path,
            "size": len(content),
            "target": f"{settings.deploy_base_dir or ''}/{target_rel}".replace("//", "/"),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Deploy upload error for %s/%s: %s", project_name, safe_rel_path, str(e))
        raise HTTPException(status_code=500, detail=f"Deploy failed: {e}")


@router.post("/{project_name}/bundle")
async def upload_project_bundle(
    project_name: str,
    bundle: UploadFile = File(..., description="Zip archive of the build output"),
    x_deploy_token: Optional[str] = Header(default=None, alias="X-Deploy-Token"),
):
    """
    Upload a zip archive of the whole project build in one request.

    The server extracts the zip and pushes all files over a single persistent
    SFTP/FTPS connection — much faster than uploading files individually.

    The client should zip the build directory contents (not the directory itself),
    so that 'index.html' lands at {project}/index.html on the remote.
    """
    _check_token(x_deploy_token, project_name)
    project_name = _sanitize_path(project_name)

    try:
        raw = await bundle.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read bundle: {e}")

    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as e:
        raise HTTPException(status_code=400, detail=f"Invalid zip file: {e}")

    client = get_deploy_client()
    uploaded = []
    failed = []

    for zip_entry in zf.infolist():
        if zip_entry.is_dir():
            continue

        # Sanitize each path component
        parts = Path(zip_entry.filename).parts
        try:
            safe_parts = [_sanitize_path(p) for p in parts]
        except HTTPException:
            logger.warning("Skipping unsafe zip entry: %s", zip_entry.filename)
            failed.append({"path": zip_entry.filename, "error": "unsafe path"})
            continue

        safe_rel = str(Path(*safe_parts))
        target_rel = f"{project_name}/{safe_rel}"

        try:
            data = zf.read(zip_entry.filename)
            client.upload_bytes(data, target_rel)
            uploaded.append(safe_rel)
            logger.info("Bundle upload OK: %s (%d bytes)", target_rel, len(data))
        except Exception as e:
            logger.error("Bundle upload failed for %s: %s", target_rel, e)
            failed.append({"path": safe_rel, "error": str(e)})

    if failed and not uploaded:
        raise HTTPException(status_code=500, detail={"message": "All files failed to upload", "failed": failed})

    return {
        "status": "success" if not failed else "partial",
        "project": project_name,
        "uploaded": len(uploaded),
        "failed": failed,
        "files": uploaded,
    }


@router.get("/health")
async def deploy_health():
    configured = bool(settings.deploy_host and settings.deploy_user)
    return {
        "status": "ok" if configured else "not_configured",
        "deploy_host": settings.deploy_host,
        "deploy_port": settings.deploy_port,
        "has_token": bool(settings.deploy_auth_token),
        "message": "POST /api/deploy/{project}/file — single file. POST /api/deploy/{project}/bundle — zip archive (faster).",
    }
