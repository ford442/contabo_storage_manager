import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .config import settings
from .models import FileUploadResponse
from .ftp_client import ftp_client

logger = logging.getLogger(__name__)

webhook_router = APIRouter(prefix="/webhook", tags=["webhooks"])
files_router = APIRouter(prefix="/files", tags=["files"])

# ====================== MIME Types ======================
MIME_MAP = {
    ".flac": "audio/flac",
    ".wav": "audio/wav",
    ".aiff": "audio/aiff",
    ".aif": "audio/aiff",
    ".mid": "audio/midi",
    ".midi": "audio/midi",
    ".mp3": "audio/mpeg",
    ".json": "application/json",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
}

# ====================== Helpers ======================
def _today_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def _ts_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")

def _verify_signature(payload: bytes, signature_header: Optional[str]) -> bool:
    """Verify HMAC signature if WEBHOOK_SECRET is set."""
    if not settings.webhook_secret:
        return True  # Signature check disabled if no secret set
    if not signature_header:
        return False  # Secret is set but no signature provided
    try:
        sig = signature_header.replace("sha256=", "").replace("sha1=", "")
        computed = hmac.new(
            settings.webhook_secret.encode(), payload, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(sig, computed)
    except Exception:
        return False

async def _save_upload(upload: UploadFile, rel_dir: str) -> dict:
    """Save file locally + optionally upload to external SFTP via paramiko."""
    base_dir = Path(settings.files_dir) / rel_dir
    base_dir.mkdir(parents=True, exist_ok=True)

    # Safe filename
    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in (upload.filename or "file"))
    timestamp = _ts_slug()
    filename = f"{timestamp}_{safe_name}"

    local_path = base_dir / filename

    # Save locally
    with open(local_path, "wb") as f:
        content = await upload.read()
        f.write(content)

    local_rel_path = f"{rel_dir}/{filename}"

    # Upload to external storage via paramiko (if configured)
    remote_path = await ftp_client.upload(local_path, local_rel_path)

    return {
        "local_path": local_rel_path,
        "remote_path": remote_path,
        "size_bytes": local_path.stat().st_size
    }


# ====================== New Endpoints ======================

@webhook_router.post("/image-effects", response_model=FileUploadResponse)
async def image_effects_webhook(
    request: Request,
    signature: Optional[str] = Header(None, alias="X-Hub-Signature-256"),
):
    # Read raw body for signature verification (must match exact bytes sent)
    body = await request.body()
    
    if not _verify_signature(body, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail="Invalid JSON")

    action = payload.get("action")
    name = payload.get("name", _ts_slug())

    if action == "save_shader":
        rel_dir = "image-effects/shaders"
    elif action == "save_metadata":
        rel_dir = "image-effects/metadata"
    elif action == "save_output":
        rel_dir = f"image-effects/outputs/{_today_slug()}"
    else:
        rel_dir = "image-effects/misc"

    # Save as proper JSON
    base_dir = Path(settings.files_dir) / rel_dir
    base_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{_ts_slug()}_{name.replace(' ', '_')}.json"
    file_path = base_dir / filename
    file_path.write_text(json.dumps(payload, indent=2))

    # Upload to external storage
    rel_path = f"{rel_dir}/{filename}"
    remote_path = await ftp_client.upload(file_path, rel_path)

    return FileUploadResponse(
        status="success",
        message=f"Saved to {rel_dir}",
        files=[rel_path],
        remote_files=[remote_path] if remote_path else None,
    )


@webhook_router.post("/flac", response_model=FileUploadResponse)
async def flac_webhook(
    request: Request,
    action: str = Form(...),
    file: Optional[UploadFile] = File(None),
):
    # For multipart uploads, signature can be in header or skipped if no secret set
    signature = request.headers.get("X-Hub-Signature-256") or request.headers.get("X-Hub-Signature")
    if settings.webhook_secret and not signature:
        raise HTTPException(status_code=401, detail="Missing signature header")
    # Note: Full body signature verification for multipart is complex; 
    # we check presence of signature when secret is configured

    saved_files = []
    remote_files = []

    if file and action == "upload_audio":
        ext = Path(file.filename or "").suffix.lower()
        if ext == ".flac":
            rel_dir = "audio/flac"
        elif ext in (".wav", ".aiff", ".aif"):
            rel_dir = "audio/wav"
        else:
            rel_dir = "audio/misc"

        result = await _save_upload(file, rel_dir)
        saved_files.append(result["local_path"])
        if result.get("remote_path"):
            remote_files.append(result["remote_path"])

    # TODO: Add handling for "save_playlist" and "save_metadata" (JSON only)

    return FileUploadResponse(
        status="success",
        message="FLAC content processed",
        files=saved_files,
        remote_files=remote_files if remote_files else None,
    )


@webhook_router.post("/sequencer", response_model=FileUploadResponse)
async def sequencer_webhook(
    request: Request,
    action: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
):
    # For multipart uploads, signature can be in header or skipped if no secret set
    signature = request.headers.get("X-Hub-Signature-256") or request.headers.get("X-Hub-Signature")
    if settings.webhook_secret and not signature:
        raise HTTPException(status_code=401, detail="Missing signature header")

    saved_files = []
    remote_files = []

    if file:
        ext = Path(file.filename or "").suffix.lower()
        if action == "upload_midi" or ext in (".mid", ".midi"):
            rel_dir = "sequencer/midi"
        elif action == "upload_sample":
            rel_dir = "sequencer/samples"
        elif action == "upload_recording":
            rel_dir = "sequencer/recordings"
        else:
            rel_dir = "sequencer/misc"

        result = await _save_upload(file, rel_dir)
        saved_files.append(result["local_path"])
        if result.get("remote_path"):
            remote_files.append(result["remote_path"])

    # TODO: Add JSON project saving for "save_project"

    return FileUploadResponse(
        status="success",
        message="Sequencer content saved",
        files=saved_files,
        remote_files=remote_files if remote_files else None,
    )


@webhook_router.post("/generic", response_model=FileUploadResponse)
async def generic_webhook(
    request: Request,
    signature: Optional[str] = Header(None, alias="X-Hub-Signature-256"),
):
    """Generic webhook that accepts any JSON with source, event, data fields."""
    body = await request.body()
    
    if not _verify_signature(body, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail="Invalid JSON")

    source = payload.get("source", "unknown")
    event = payload.get("event", "unknown")
    
    # Save to webhooks/generic/
    rel_dir = f"webhooks/{source}"
    base_dir = Path(settings.files_dir) / rel_dir
    base_dir.mkdir(parents=True, exist_ok=True)
    
    filename = f"{_ts_slug()}_{source}_{event}.json"
    file_path = base_dir / filename
    file_path.write_text(json.dumps(payload, indent=2))
    
    rel_path = f"{rel_dir}/{filename}"
    remote_path = await ftp_client.upload(file_path, rel_path)

    return FileUploadResponse(
        status="success",
        message=f"Saved to {rel_dir}",
        files=[rel_path],
        remote_files=[remote_path] if remote_path else None,
    )


@webhook_router.post("/github", response_model=FileUploadResponse)
async def github_webhook(
    request: Request,
    x_github_event: Optional[str] = Header(None),
    signature: Optional[str] = Header(None, alias="X-Hub-Signature-256"),
):
    """GitHub webhook handler."""
    body = await request.body()
    
    if not _verify_signature(body, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail="Invalid JSON")

    event = x_github_event or "unknown"
    repo = payload.get("repository", {}).get("full_name", "unknown")
    
    # Save to webhooks/github/
    rel_dir = "webhooks/github"
    base_dir = Path(settings.files_dir) / rel_dir
    base_dir.mkdir(parents=True, exist_ok=True)
    
    filename = f"{_ts_slug()}_{repo.replace('/', '_')}_{event}.json"
    file_path = base_dir / filename
    file_path.write_text(json.dumps(payload, indent=2))
    
    rel_path = f"{rel_dir}/{filename}"
    remote_path = await ftp_client.upload(file_path, rel_path)

    return FileUploadResponse(
        status="success",
        message=f"GitHub event saved",
        files=[rel_path],
        remote_files=[remote_path] if remote_path else None,
    )


# ====================== Static File Serving ======================
@files_router.get("/{file_path:path}", summary="Serve stored files with correct MIME")
async def serve_file(file_path: str):
    """Serve files from the storage directory with proper MIME types."""
    base = Path(settings.files_dir).resolve()
    target = (base / file_path).resolve()

    # Prevent path traversal
    if not str(target).startswith(str(base)):
        raise HTTPException(status_code=403, detail="Forbidden")

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")

    suffix = target.suffix.lower()
    media_type = MIME_MAP.get(suffix, "application/octet-stream")

    return FileResponse(target, media_type=media_type)
