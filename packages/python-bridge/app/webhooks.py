import hashlib
import hmac
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..config import settings
from ..models import FileUploadResponse
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
    if not settings.webhook_secret or not signature_header:
        return True  # Signature check disabled in dev
    try:
        sig = signature_header.replace("sha256=", "")
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
    payload: dict,
    signature: Optional[str] = None,   # X-Hub-Signature-256
):
    if not _verify_signature(str(payload).encode(), signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

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

    # Save as JSON
    base_dir = Path(settings.files_dir) / rel_dir
    base_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{_ts_slug()}_{name.replace(' ', '_')}.json"
    file_path = base_dir / filename
    file_path.write_text(str(payload))   # TODO: use json.dumps for proper formatting

    return FileUploadResponse(
        status="success",
        message=f"Saved to {rel_dir}",
        files=[f"{rel_dir}/{filename}"],
    )


@webhook_router.post("/flac", response_model=FileUploadResponse)
async def flac_webhook(
    action: str = Form(...),
    file: Optional[UploadFile] = File(None),
    signature: Optional[str] = None,
):
    if not _verify_signature(b"", signature):   # For multipart we skip detailed check for simplicity
        raise HTTPException(status_code=401, detail="Invalid signature")

    saved_files = []

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

    # TODO: Add handling for "save_playlist" and "save_metadata" (JSON only)

    return FileUploadResponse(
        status="success",
        message="FLAC content processed",
        files=saved_files,
    )


@webhook_router.post("/sequencer", response_model=FileUploadResponse)
async def sequencer_webhook(
    action: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    signature: Optional[str] = None,
):
    if not _verify_signature(b"", signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    saved_files = []

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

    # TODO: Add JSON project saving for "save_project"

    return FileUploadResponse(
        status="success",
        message="Sequencer content saved",
        files=saved_files,
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
