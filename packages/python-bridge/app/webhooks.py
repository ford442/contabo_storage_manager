"""Webhook router: receives JSON payloads / file uploads and persists them.

Supports:
  - Generic, Shopify, GitHub webhooks (original)
  - image_video_effects  →  POST /webhook/image-effects
  - flac_player          →  POST /webhook/flac
  - web_sequencer        →  POST /webhook/sequencer  (JSON or multipart)

Static file server:
  - GET /files/{path}    →  serves FILES_DIR with correct MIME types
"""

from __future__ import annotations

import hmac
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse

from .config import get_settings
from .ftp_client import upload_bytes
from .logger import get_logger
from .models import FileUploadResponse, WebhookPayload, WebhookResponse
from .ftp_client import ftp_client   # ← Add this import

router = APIRouter(prefix="/webhook", tags=["webhooks"])
files_router = APIRouter(prefix="/files", tags=["files"])

log = get_logger("webhooks")
settings = get_settings()


# ── MIME type map ─────────────────────────────────────────────────────────────

MIME_MAP: dict[str, str] = {
    ".flac": "audio/flac",
    ".wav":  "audio/wav",
    ".aiff": "audio/aiff",
    ".aif":  "audio/aiff",
    ".mid":  "audio/midi",
    ".midi": "audio/midi",
    ".mp3":  "audio/mpeg",
    ".ogg":  "audio/ogg",
    ".json": "application/json",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
    ".gif":  "image/gif",
    ".webp": "image/webp",
    ".svg":  "image/svg+xml",
    ".mp4":  "video/mp4",
    ".webm": "video/webm",
    ".sf2":  "application/octet-stream",  # SoundFont
    ".sf3":  "application/octet-stream",
}


# ── Slug / timestamp helpers ──────────────────────────────────────────────────

def _today_slug() -> str:
    """Return current UTC date as YYYY-MM-DD."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _ts_slug() -> str:
    """Return current UTC timestamp as YYYYMMDDTHHMMSSffffff."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")


_SAFE_NAME_RE = re.compile(r"[^\w\-]")


def _safe_name(value: str, max_len: int = 64) -> str:
    """Return a filesystem-safe name (alphanumeric + hyphens/underscores only)."""
    return _SAFE_NAME_RE.sub("_", value)[:max_len]


# ── Signature verification ────────────────────────────────────────────────────

def _verify_signature(body: bytes, signature_header: str | None) -> None:
    """Raise HTTP 401 if WEBHOOK_SECRET is set and signature does not match."""
    secret = settings.webhook_secret
    if not secret:
        return  # verification disabled (development mode)

    if not signature_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing signature header",
        )

    algo = settings.webhook_hmac_algo
    expected_sig = hmac.new(secret.encode(), body, algo).hexdigest()
    # Support both 'sha256=<hex>' style (GitHub/Shopify) and bare hex
    provided = signature_header.split("=", 1)[-1]

    if not hmac.compare_digest(expected_sig, provided):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )


# ── Persistence helpers ───────────────────────────────────────────────────────

def _save_payload(
    payload: WebhookPayload,
    raw: bytes,
    *,
    custom_rel_path: str | None = None,
) -> str:
    """Write a JSON payload to the local volume and (optionally) FTP.

    Returns the relative path where the file was stored.
    """
    if custom_rel_path:
        rel_path = custom_rel_path
    else:
        ts = _ts_slug()
        safe_source = _safe_name(payload.source)
        safe_event = _safe_name(payload.event.replace(".", "_"))
        filename = f"{safe_source}_{safe_event}_{ts}.json"
        rel_path = f"webhooks/{safe_source}/{filename}"

    files_dir = Path(settings.files_dir)
    local_path = files_dir / rel_path
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(raw)
    log.info("Saved payload → %s", local_path)

    try:
        upload_bytes(raw, rel_path)
    except Exception as exc:
        log.warning("FTP upload failed (payload still saved locally): %s", exc)

    return rel_path


async def _save_upload(upload: UploadFile, rel_dir: str) -> dict:
    """
    Save uploaded file locally + optionally upload to external FTP/SFTP.
    Returns dict with local and remote paths.
    """
    base_dir = Path(settings.files_dir) / rel_dir
    base_dir.mkdir(parents=True, exist_ok=True)

    # Create safe filename
    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in (upload.filename or "file"))
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    filename = f"{timestamp}_{safe_name}"
    
    local_path = base_dir / filename

    # Save locally
    with open(local_path, "wb") as f:
        content = await upload.read()
        f.write(content)

    local_rel_path = f"{rel_dir}/{filename}"

    # Upload to external FTP/SFTP if configured
    remote_path = await ftp_client.upload(local_path, local_rel_path)

    return {
        "local_path": str(local_rel_path),
        "remote_path": remote_path,
        "size": local_path.stat().st_size
    }


# ══════════════════════════════════════════════════════════════════════════════
# Original webhook routes (unchanged)
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/generic",
    response_model=WebhookResponse,
    summary="Generic JSON webhook receiver",
)
async def webhook_generic(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
):
    """Accept any JSON body with ``source``, ``event``, and ``data`` fields."""
    body = await request.body()
    _verify_signature(body, x_hub_signature_256)

    try:
        data = json.loads(body)
        payload = WebhookPayload(**data)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    rel_path = _save_payload(payload, body)
    return WebhookResponse(status="ok", message="Payload received", file=rel_path)


@router.post(
    "/shopify",
    response_model=WebhookResponse,
    summary="Shopify webhook receiver",
)
async def webhook_shopify(
    request: Request,
    x_shopify_hmac_sha256: str | None = Header(default=None),
):
    body = await request.body()
    _verify_signature(body, x_shopify_hmac_sha256)

    try:
        raw_data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    topic = request.headers.get("x-shopify-topic", "unknown.event")
    payload = WebhookPayload(source="shopify", event=topic, data=raw_data)
    rel_path = _save_payload(payload, body)
    return WebhookResponse(status="ok", message="Shopify payload received", file=rel_path)


@router.post(
    "/github",
    response_model=WebhookResponse,
    summary="GitHub webhook receiver",
)
async def webhook_github(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
):
    body = await request.body()
    _verify_signature(body, x_hub_signature_256)

    try:
        raw_data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    event = x_github_event or "unknown"
    payload = WebhookPayload(source="github", event=event, data=raw_data)
    rel_path = _save_payload(payload, body)
    return WebhookResponse(status="ok", message="GitHub payload received", file=rel_path)


# ══════════════════════════════════════════════════════════════════════════════
# App-specific webhook routes
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/image-effects",
    response_model=FileUploadResponse,
    summary="image_video_effects — save shader configs, metadata, or outputs",
)
async def webhook_image_effects(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
):
    """JSON body.  Expected fields:

    - ``action``: one of ``save_shader`` | ``save_metadata`` | ``save_output``
    - ``name``:   human-readable name used as the filename slug (optional)
    - ``data``:   arbitrary payload stored as JSON

    Folder routing:

    .. code-block::

        save_shader   → image-effects/shaders/<name>.json
        save_metadata → image-effects/metadata/<name>.json
        save_output   → image-effects/outputs/YYYY-MM-DD/<name>.json
        (other)       → image-effects/misc/<name>.json
    """
    body = await request.body()
    _verify_signature(body, x_hub_signature_256)

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    action = data.get("action", "unknown")
    raw_name = data.get("name") or _ts_slug()
    name = _safe_name(str(raw_name))
    ts = _ts_slug()

    action_dir_map = {
        "save_shader":   "image-effects/shaders",
        "save_metadata": "image-effects/metadata",
        "save_output":   f"image-effects/outputs/{_today_slug()}",
    }
    rel_dir = action_dir_map.get(action, "image-effects/misc")
    rel_path = f"{rel_dir}/{ts}_{name}.json"

    wh_payload = WebhookPayload(source="image-effects", event=action, data=data)
    _save_payload(wh_payload, body, custom_rel_path=rel_path)

    log.info("image-effects: action=%s → %s", action, rel_path)
    return FileUploadResponse(status="ok", message=f"image-effects '{action}' saved", files=[rel_path])


@router.post(
    "/flac",
    response_model=FileUploadResponse,
    summary="flac_player — upload audio files, cover art, or playlists",
)
async def webhook_flac(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    action: str = Form(..., description="upload_audio | upload_cover | save_playlist | save_metadata"),
    file: Optional[UploadFile] = File(default=None, description="Binary audio / image file"),
):
    """Multipart form upload.

    ``action`` values and their destinations:

    .. code-block::

        upload_audio  (.flac)          → audio/flac/
        upload_audio  (.wav / .aiff)   → audio/wav/
        upload_audio  (other)          → audio/misc/
        upload_cover                   → audio/covers/
        save_playlist                  → audio/playlists/  (JSON body in 'data' field)
        save_metadata                  → audio/metadata/   (JSON body in 'data' field)

    Signature is verified against the raw multipart body.
    """
    body = await request.body()
    _verify_signature(body, x_hub_signature_256)

    saved_files: list[str] = []

    if file is not None and file.filename:
        ext = Path(file.filename).suffix.lower()

        if action == "upload_audio":
            if ext == ".flac":
                rel_dir = "audio/flac"
            elif ext in (".wav", ".aiff", ".aif"):
                rel_dir = "audio/wav"
            else:
                rel_dir = "audio/misc"
        elif action == "upload_cover":
            rel_dir = "audio/covers"
        else:
            rel_dir = "audio/misc"

        rel_path = await _save_upload(file, rel_dir)
        saved_files.append(rel_path)
        log.info("flac: action=%s ext=%s → %s", action, ext, rel_path)
    else:
        # No file attached — treat as metadata / playlist JSON stored from form fields
        ts = _ts_slug()
        if action == "save_playlist":
            rel_dir = "audio/playlists"
        else:
            rel_dir = "audio/metadata"
        rel_path = f"{rel_dir}/{ts}_{action}.json"
        wh_payload = WebhookPayload(source="flac", event=action, data={})
        _save_payload(wh_payload, body, custom_rel_path=rel_path)
        saved_files.append(rel_path)
        log.info("flac: action=%s (no file) → %s", action, rel_path)

    return FileUploadResponse(status="ok", message=f"flac '{action}' processed", files=saved_files)


@router.post(
    "/sequencer",
    response_model=FileUploadResponse,
    summary="web_sequencer — save projects (JSON) or upload MIDI/samples/recordings",
)
async def webhook_sequencer(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
):
    """Accepts **either** ``application/json`` or ``multipart/form-data``.

    **JSON** (``Content-Type: application/json``):

    - ``action``: ``save_project``
    - ``name``:   project slug
    - ``data``:   full project JSON → ``sequencer/projects/<name>.json``

    **Multipart** (``Content-Type: multipart/form-data``):

    - ``action`` form field: ``upload_midi`` | ``upload_sample`` | ``upload_recording``
    - ``file`` field: the binary file

    Folder routing:

    .. code-block::

        save_project    → sequencer/projects/<name>.json
        upload_midi     → sequencer/midi/
        upload_sample   → sequencer/samples/
        upload_recording→ sequencer/recordings/
    """
    body = await request.body()
    _verify_signature(body, x_hub_signature_256)

    content_type = request.headers.get("content-type", "")
    saved_files: list[str] = []

    if "application/json" in content_type:
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

        action = data.get("action", "save_project")
        raw_name = data.get("name") or _ts_slug()
        name = _safe_name(str(raw_name))
        ts = _ts_slug()
        rel_path = f"sequencer/projects/{ts}_{name}.json"

        wh_payload = WebhookPayload(source="sequencer", event=action, data=data)
        _save_payload(wh_payload, body, custom_rel_path=rel_path)
        saved_files.append(rel_path)
        log.info("sequencer: action=%s → %s", action, rel_path)

    elif "multipart/form-data" in content_type:
        form = await request.form()
        action = str(form.get("action", "upload_misc"))
        file = form.get("file")

        if not isinstance(file, UploadFile) or not file.filename:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Multipart request must include a 'file' field with a filename",
            )

        action_dir_map = {
            "upload_midi":      "sequencer/midi",
            "upload_sample":    "sequencer/samples",
            "upload_recording": "sequencer/recordings",
        }
        rel_dir = action_dir_map.get(action, "sequencer/misc")
        rel_path = await _save_upload(file, rel_dir)
        saved_files.append(rel_path)
        log.info("sequencer: action=%s → %s", action, rel_path)

    else:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Content-Type must be application/json or multipart/form-data",
        )

    return FileUploadResponse(status="ok", message="sequencer data processed", files=saved_files)


# ══════════════════════════════════════════════════════════════════════════════
# Static file server
# ══════════════════════════════════════════════════════════════════════════════

@files_router.get(
    "/{file_path:path}",
    summary="Serve stored files with correct MIME types",
    response_class=FileResponse,
)
async def serve_file(file_path: str):
    """Serve any file under ``FILES_DIR`` with the correct ``Content-Type``.

    Path traversal is prevented by resolving both the base directory and the
    requested target and confirming the target is a strict child of the base.
    """
    base = Path(settings.files_dir).resolve()
    target = (base / file_path).resolve()

    # Security: reject any path that escapes the files directory
    if not str(target).startswith(str(base) + "/") and target != base:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    suffix = target.suffix.lower()
    media_type = MIME_MAP.get(suffix, "application/octet-stream")
    log.debug("Serving %s as %s", target, media_type)
    return FileResponse(str(target), media_type=media_type)
