#!/usr/bin/env python3
"""
deploy_router.py - Project deployment endpoint for contabo_storage_manager

Allows per-project deploy.py scripts (in other repos) to upload built files
to https://storage.noahcohn.com/api/deploy/{project}/file

The server then uses the protected DEPLOY_* credentials (stored only in .env on VPS)
to upload to the target (e.g. storage.1ink.us via SFTP/FTP).

This way, no FTP/SFTP passwords ever need to be stored in individual project repos.
"""

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
    """Lazily create a StorageFTPClient configured for the deploy target."""
    global _deploy_client
    if _deploy_client is None:
        if not settings.deploy_host:
            logger.warning("DEPLOY_HOST not configured - deploys will be skipped")
        _deploy_client = StorageFTPClient(
            host=settings.deploy_host,
            user=settings.deploy_user,
            password=settings.deploy_pass,
            port=settings.deploy_port,
            base_dir=settings.deploy_base_dir,
        )
    return _deploy_client


def _sanitize_path(name: str) -> str:
    """Basic sanitization to prevent path traversal."""
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
    rel_path: str = Form(..., description="Relative path inside the project, e.g. 'js/app.js' or 'index.html'"),
    x_deploy_token: Optional[str] = Header(default=None, alias="X-Deploy-Token"),
):
    """
    Upload a single file for a project deployment.

    The client (deploy.py in your project) calls this for every file in dist/.
    Server-side it uploads to DEPLOY_BASE_DIR/{project_name}/{rel_path}
    using the protected credentials that never leave the VPS.
    """
    if settings.deploy_auth_token:
        if not x_deploy_token or x_deploy_token != settings.deploy_auth_token:
            logger.warning("Deploy attempt with invalid/missing token for project=%s", project_name)
            raise HTTPException(status_code=403, detail="Invalid or missing X-Deploy-Token")

    project_name = _sanitize_path(project_name)
    rel_parts = [_sanitize_path(p) for p in Path(rel_path).parts]
    safe_rel_path = str(Path(*rel_parts))

    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    try:
        content = await file.read()
        client = get_deploy_client()
        target_rel = f"{project_name}/{safe_rel_path}"
        success = client.upload_bytes(content, target_rel)

        if success:
            logger.info("Deploy upload successful: project=%s path=%s size=%d",
                        project_name, safe_rel_path, len(content))
            return {
                "status": "success",
                "project": project_name,
                "rel_path": safe_rel_path,
                "size": len(content),
                "target": f"{settings.deploy_base_dir or ''}/{target_rel}".replace("//", "/"),
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to upload to deploy target storage")

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Deploy upload error for %s/%s: %s", project_name, safe_rel_path, str(e))
        raise HTTPException(status_code=500, detail=f"Deploy failed: {str(e)}")


@router.get("/health")
async def deploy_health():
    """Quick health check for the deploy system."""
    configured = bool(settings.deploy_host and settings.deploy_user)
    return {
        "status": "ok" if configured else "not_configured",
        "deploy_host": settings.deploy_host,
        "deploy_port": settings.deploy_port,
        "has_token": bool(settings.deploy_auth_token),
        "message": "Use POST /api/deploy/{project}/file to deploy builds.",
    }
