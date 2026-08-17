import hashlib
import hmac
import json
import logging
import shutil
import subprocess
import uuid
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from .config import settings
from .models import FileUploadResponse
from .ftp_client import ftp_client
from .notes_router import _slugify
from pydub import AudioSegment
from pydub.exceptions import CouldntDecodeError

logger = logging.getLogger(__name__)
MAX_SHADER_LIST_FILES = 50
MAX_SHADER_LIST_FILE_SIZE_BYTES = 5 * 1024 * 1024

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
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    ".json": "application/json",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    # Tracker modules (mod-player)
    ".mod": "audio/x-mod",
    ".xm": "audio/x-xm",
    ".s3m": "audio/x-s3m",
    ".it": "audio/x-it",
    ".mptm": "audio/x-mod",
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

async def _save_upload(upload: UploadFile, rel_dir: str, remote_rel_dir: Optional[str] = None) -> dict:
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
    remote_rel_path = f"{remote_rel_dir}/{filename}" if remote_rel_dir else local_rel_path

    # Upload to external storage via paramiko (if configured) — non-fatal on failure,
    # since the file is already saved locally and served from storage.noahcohn.com/files/
    remote_path = None
    try:
        remote_path = await ftp_client.upload(local_path, remote_rel_path)
    except Exception as exc:
        logger.warning("FTP upload failed for %s (non-fatal): %s", local_rel_path, exc)

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
    # For browser apps like cloud_notes, full body signature verification is
    # impossible without exposing the secret. Follow the same pattern as
    # /webhook/flac and /webhook/sequencer: only require the header to be
    # present when a webhook secret is configured.
    signature = request.headers.get("X-Hub-Signature-256") or request.headers.get("X-Hub-Signature")
    if settings.webhook_secret and not signature:
        raise HTTPException(status_code=401, detail="Missing signature header")

    body = await request.body()

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


@webhook_router.post("/image-effects/generate-shader-lists", response_model=FileUploadResponse)
async def generate_shader_lists_webhook(
    x_webhook_token: Optional[str] = Header(None, alias="X-Webhook-Token"),
):
    expected_token = settings.shader_generation_token
    if not expected_token:
        raise HTTPException(status_code=503, detail="Shader generation token is not configured")
    if not x_webhook_token or not hmac.compare_digest(x_webhook_token, expected_token):
        raise HTTPException(status_code=401, detail="Invalid webhook token")

    repo_dir = Path(settings.image_effects_repo_dir).expanduser().resolve()
    script_path = (repo_dir / "scripts" / "generate_shader_lists.js").resolve()
    shader_lists_dir = (repo_dir / settings.image_effects_shader_lists_dir).resolve()

    if not repo_dir.exists():
        raise HTTPException(status_code=404, detail=f"Repo directory not found: {repo_dir}")
    if not (repo_dir / ".git").exists():
        raise HTTPException(status_code=400, detail=f"Not a git repository: {repo_dir}")
    if not script_path.is_relative_to(repo_dir):
        raise HTTPException(status_code=400, detail=f"Script path escapes repo directory: {script_path}")
    if not shader_lists_dir.is_relative_to(repo_dir):
        raise HTTPException(status_code=400, detail=f"Shader list path escapes repo directory: {shader_lists_dir}")
    if not script_path.exists():
        raise HTTPException(status_code=404, detail=f"Shader list script not found: {script_path}")

    pull_result = await asyncio.to_thread(
        subprocess.run,
        ["git", "-c", "core.hooksPath=/dev/null", "-C", str(repo_dir), "pull", "--ff-only"],
        capture_output=True,
        text=True,
        check=False,
    )
    if pull_result.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"git pull failed: {pull_result.stderr.strip() or pull_result.stdout.strip() or 'unknown error'}",
        )

    generate_result = await asyncio.to_thread(
        subprocess.run,
        ["node", str(script_path)],
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
        check=False,
    )
    if generate_result.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=(
                "shader list generation failed: "
                f"{generate_result.stderr.strip() or generate_result.stdout.strip() or 'unknown error'}"
            ),
        )

    if not shader_lists_dir.exists():
        raise HTTPException(status_code=500, detail=f"Shader list output directory not found: {shader_lists_dir}")

    generated_files = sorted(list(shader_lists_dir.glob("*.json")))
    if not generated_files:
        raise HTTPException(status_code=500, detail="No shader list JSON files were generated")
    if len(generated_files) > MAX_SHADER_LIST_FILES:
        raise HTTPException(status_code=500, detail=f"Too many shader list files (max {MAX_SHADER_LIST_FILES})")

    rel_dir = "image-effects/shader-lists"
    output_dir = Path(settings.files_dir) / rel_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    files = []
    remote_files = []
    for generated in generated_files:
        size = generated.stat().st_size
        if size > MAX_SHADER_LIST_FILE_SIZE_BYTES:
            raise HTTPException(
                status_code=500,
                detail=f"Shader list file too large: {generated.name} ({size} bytes)",
            )
        destination = output_dir / generated.name
        try:
            await asyncio.to_thread(shutil.copy2, generated, destination)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Failed to copy {generated.name}: {exc}") from exc
        rel_path = f"{rel_dir}/{generated.name}"
        files.append(rel_path)
        remote_path = await ftp_client.upload(destination, rel_path)
        if remote_path:
            remote_files.append(remote_path)

    return FileUploadResponse(
        status="success",
        message=f"Generated and uploaded {len(files)} shader list file(s)",
        files=files,
        remote_files=remote_files if remote_files else None,
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
            # Save locally to audio/music (consistent with /api/songs/upload)
            # and mirror remotely to external_flac_dir
            # Normalize to 16-bit / 44.1kHz / stereo FLAC
            content = await file.read()
            temp_path = Path("/tmp") / f"{uuid.uuid4()}_{file.filename}"
            temp_path.write_bytes(content)
            try:
                audio = AudioSegment.from_file(str(temp_path))
            except (CouldntDecodeError, FileNotFoundError):
                raise HTTPException(status_code=400, detail="Could not decode file. Is ffmpeg installed?")

            base = Path(settings.files_dir)
            music_dir = base / "audio" / "music"
            music_dir.mkdir(parents=True, exist_ok=True)
            storage_filename = f"{uuid.uuid4().hex[:8]}_{temp_path.stem}.flac"
            dest = music_dir / storage_filename
            audio = audio.set_frame_rate(44100)
            audio = audio.set_channels(2)
            audio = audio.set_sample_width(3)  # 24-bit (stored as s32)
            audio.export(dest, format="flac", parameters=["-compression_level", "8"])
            temp_path.unlink(missing_ok=True)

            local_rel_path = f"audio/music/{storage_filename}"
            remote_rel_path = f"{settings.external_flac_dir}/{storage_filename}"
            remote_path = await ftp_client.upload(dest, remote_rel_path)

            result = {
                "local_path": local_rel_path,
                "remote_path": remote_path,
                "size_bytes": dest.stat().st_size
            }
        elif ext in (".wav", ".aiff", ".aif"):
            rel_dir = "audio/wav"
            result = await _save_upload(file, rel_dir)
        else:
            rel_dir = "audio/misc"
            result = await _save_upload(file, rel_dir)

        saved_files.append(result["local_path"])
        if result.get("remote_path"):
            remote_files.append(result["remote_path"])

        # --- Auto-index into local songs.json for immediate library availability ---
        from .api import _load_songs, _save_songs

        songs = _load_songs()
        raw_title = Path(file.filename or "").stem.replace("_", " ").replace("-", " ")
        title = raw_title.strip() or "Untitled"
        song_id = str(uuid.uuid4())[:8]
        storage_filename = result["local_path"].rsplit("/", 1)[-1]

        song = {
            "id": song_id,
            "name": f"{title}{ext}",
            "title": title,
            "author": "Unknown",
            "genre": None,
            "rating": None,
            "description": f"Uploaded via webhook on {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
            "tags": [],
            "duration": None,
            "play_count": 0,
            "last_played": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "filename": storage_filename,
            "url": f"/api/music/{song_id}",
            "size": result["size_bytes"],
        }
        songs.append(song)
        _save_songs(songs)
        logger.info("Auto-indexed webhook upload %s -> %s", storage_filename, song_id)

        # Song metadata is written directly to local songs.json;
        # the static React flac_player app reads from /api/songs.

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


@webhook_router.post("/notes", response_model=FileUploadResponse)
async def notes_webhook(
    request: Request,
    signature: Optional[str] = Header(None, alias="X-Hub-Signature-256"),
):
    """Cloud Notes webhook - receives structured note data from cloud_notes app.
    
    Stores notes as timestamped JSON files under notes/webhook/ directory.
    Supports encrypted content that the frontend decrypts client-side.
    """
    # Browser apps like cloud_notes cannot compute HMAC signatures without
    # exposing the webhook secret in client-side code. This endpoint is
    # intentionally open for direct browser-to-server sync.
    body = await request.body()

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail="Invalid JSON")

    # Validate required fields
    source = payload.get("source", "cloud_notes")
    event = payload.get("event", "note.unknown")
    data = payload.get("data", {})
    
    if not data:
        raise HTTPException(status_code=422, detail="Missing data field")

    # Extract note fields
    note_id = data.get("id") or _ts_slug()
    title = data.get("title", "Untitled")
    content = data.get("content", "")
    subject = data.get("subject", "General")
    section = data.get("section", "Inbox")
    tags = data.get("tags", "")
    author = data.get("author", "User")
    description = data.get("description", "")
    updated_at = data.get("updatedAt") or datetime.now(timezone.utc).isoformat()

    # Build note JSON structure
    note_data = {
        "id": note_id,
        "title": title,
        "content": content,  # May be encrypted (ENC:v1:...)
        "subject": subject,
        "section": section,
        "tags": tags,
        "author": author,
        "description": description,
        "updatedAt": updated_at,
        "source": source,
        "event": event,
        "receivedAt": datetime.now(timezone.utc).isoformat()
    }

    # Save to notes/webhook/ directory
    rel_dir = "notes/webhook"
    base_dir = Path(settings.files_dir) / rel_dir
    base_dir.mkdir(parents=True, exist_ok=True)
    
    # Use timestamp + sanitized title as filename
    safe_title = "".join(c if c.isalnum() or c in "._-" else "_" for c in title)
    filename = f"{_ts_slug()}_{safe_title}.json"
    file_path = base_dir / filename
    file_path.write_text(json.dumps(note_data, indent=2), encoding="utf-8")
    
    rel_path = f"{rel_dir}/{filename}"
    
    # Also save as a simple markdown file for easy access
    # Extract/decrypt hint for markdown version (if encrypted, note it)
    md_content = content
    if content.startswith("ENC:v1:"):
        md_content = f"<!-- Encrypted content - use cloud_notes app to decrypt -->\n\n<!-- {content[:50]}... -->"
    
    md_dir = Path(settings.files_dir) / "notes"
    md_dir.mkdir(parents=True, exist_ok=True)
    slug = _slugify(title)
    md_filename = f"{slug}.md"
    md_path = md_dir / md_filename
    
    # Build markdown with frontmatter
    md_output = f"""---
id: {note_id}
title: {title}
subject: {subject}
section: {section}
tags: {tags}
author: {author}
updatedAt: {updated_at}
---

{md_content}
"""
    md_path.write_text(md_output, encoding="utf-8")
    md_rel_path = f"notes/{md_filename}"
    
    # Upload to external storage (non-fatal)
    remote_path = None
    md_remote_path = None
    try:
        remote_path = await ftp_client.upload(file_path, rel_path)
    except Exception as exc:
        logger.warning("FTP upload failed for note JSON (non-fatal): %s", exc)
    try:
        md_remote_path = await ftp_client.upload(md_path, md_rel_path)
    except Exception as exc:
        logger.warning("FTP upload failed for note markdown (non-fatal): %s", exc)

    return FileUploadResponse(
        status="success",
        message=f"Note saved: {title}",
        files=[rel_path, md_rel_path],
        remote_files=[p for p in [remote_path, md_remote_path] if p],
    )


@webhook_router.options("/clip-stacker")
async def clip_stacker_options():
    """Handle OPTIONS preflight for clip-stacker endpoints."""
    return Response(
        status_code=204,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS, HEAD",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
            "Access-Control-Max-Age": "86400",
        }
    )


@webhook_router.post("/clip-stacker", response_model=dict)
async def clip_stacker_save(request: Request):
    """Save a clip-stacker project.
    
    Browser apps like clip_stacker cannot compute HMAC signatures without
    exposing the webhook secret in client-side code. This endpoint is
    intentionally open for direct browser-to-server sync.
    
    Expected JSON body:
    {
        "name": "my-project",
        "payload": {
            "clips": [...],
            "transitions": [...]
        }
    }
    """
    body = await request.body()

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail="Invalid JSON")

    # Extract required fields
    project_name = data.get("name")
    if not project_name:
        raise HTTPException(status_code=400, detail="Missing 'name' field")
    
    payload = data.get("payload")
    if payload is None:
        raise HTTPException(status_code=400, detail="Missing 'payload' field")

    # Reject path traversal attempts before sanitization
    if ".." in project_name or "/" in project_name or "\\" in project_name:
        raise HTTPException(status_code=400, detail="Path traversal is not allowed")

    # Sanitize project name (replace special chars with underscores)
    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in project_name)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid project name")

    # Ensure project directory exists
    projects_dir = Path(settings.files_dir) / "clip-stacker" / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)

    # Save project JSON
    project_file = projects_dir / f"{safe_name}.json"
    project_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # Upload to external storage
    rel_path = f"clip-stacker/projects/{safe_name}.json"
    remote_path = None
    try:
        remote_path = await ftp_client.upload(project_file, rel_path)
    except Exception as exc:
        logger.warning("FTP upload failed for clip-stacker project (non-fatal): %s", exc)

    return {
        "status": "success",
        "message": f"Project saved: {safe_name}",
        "name": safe_name,
        "local_path": rel_path,
        "remote_path": remote_path,
    }


@webhook_router.get("/clip-stacker", response_model=dict)
async def clip_stacker_load(request: Request, name: str = None):
    """Load a clip-stacker project by name, or list all projects if no name is provided.

    Query parameter:
    - name: Project name (optional). When omitted, returns a list of all saved projects.

    Responses:
    - With name: { "payload": { "clips": [...], "transitions": [...] } }
    - Without name: { "projects": [{ "name": "...", "modified": <timestamp> }, ...] }
    """
    projects_dir = Path(settings.files_dir) / "clip-stacker" / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)

    if not name:
        # List all projects
        projects = []
        for f in sorted(projects_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                projects.append({
                    "name": data.get("name", f.stem),
                    "modified": f.stat().st_mtime,
                })
            except Exception:
                pass
        return {"projects": projects}

    # Reject path traversal attempts before sanitization
    if ".." in name or "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="Path traversal is not allowed")

    # Sanitize project name (replace special chars with underscores)
    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid project name")

    # Load project file
    project_file = projects_dir / f"{safe_name}.json"

    # Ensure the resolved path is within projects_dir (prevent traversal)
    try:
        project_file_resolved = project_file.resolve()
        projects_dir_resolved = projects_dir.resolve()
        if not project_file_resolved.is_relative_to(projects_dir_resolved):
            raise HTTPException(status_code=403, detail="Forbidden")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=403, detail="Forbidden")

    if not project_file.exists():
        raise HTTPException(status_code=404, detail=f"Project '{name}' not found")

    try:
        project_data = json.loads(project_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.error("Failed to load project %s: %s", safe_name, exc)
        raise HTTPException(status_code=500, detail="Failed to load project")

    # Return just the payload as per the spec
    return {"payload": project_data.get("payload", {})}


@webhook_router.delete("/clip-stacker", response_model=dict)
async def clip_stacker_delete(request: Request, name: str = None, deleteMedia: bool = False):
    """Delete a clip-stacker project by name.

    Query parameters:
    - name: Project name (required)
    - deleteMedia: If true, attempt to delete associated media files (optional, default: false)
    """
    if not name:
        raise HTTPException(status_code=400, detail="Missing 'name' query parameter")

    if ".." in name or "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="Path traversal is not allowed")

    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid project name")

    projects_dir = Path(settings.files_dir) / "clip-stacker" / "projects"
    project_file = projects_dir / f"{safe_name}.json"

    try:
        project_file_resolved = project_file.resolve()
        projects_dir_resolved = projects_dir.resolve()
        if not project_file_resolved.is_relative_to(projects_dir_resolved):
            raise HTTPException(status_code=403, detail="Forbidden")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=403, detail="Forbidden")

    if not project_file.exists():
        raise HTTPException(status_code=404, detail=f"Project '{name}' not found")

    # Try to extract clip IDs from project for media cleanup
    deleted_media_count = 0
    if deleteMedia:
        try:
            project_data = json.loads(project_file.read_text(encoding="utf-8"))
            clips = project_data.get("payload", {}).get("clips", [])
            media_dir = Path(settings.files_dir) / "clip-stacker" / "media"
            
            # Attempt to delete media files associated with each clip
            for clip in clips:
                clip_id = clip.get("id")
                if clip_id:
                    # Look for files starting with the clip ID
                    for media_file in media_dir.glob(f"{clip_id}-*"):
                        try:
                            media_file.unlink()
                            deleted_media_count += 1
                        except Exception as exc:
                            logger.warning("Failed to delete associated media %s: %s", media_file.name, exc)
        except Exception as exc:
            logger.warning("Error during media cleanup for project %s: %s", safe_name, exc)
            # Don't block project deletion on cleanup errors (best-effort)

    project_file.unlink()
    result = {"status": "success", "message": f"Project deleted: {safe_name}"}
    if deleteMedia:
        result["deleted_media_count"] = deleted_media_count
    return result


@webhook_router.options("/clip-stacker/media")
async def clip_stacker_media_options():
    """Handle OPTIONS preflight for media upload endpoint."""
    return Response(
        status_code=204,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS, HEAD",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
            "Access-Control-Max-Age": "86400",
        }
    )


@webhook_router.post("/clip-stacker/media", response_model=dict)
async def clip_stacker_media_upload(
    request: Request,
    file: UploadFile = File(...),
    name: str = Form(...),
):
    """Upload a media file for clip-stacker.

    Form fields:
    - file: Binary media file (required)
    - name: Desired filename hint (required)

    Response:
    {
        "url": "https://storage.example.com/files/clip-stacker/media/..."
    }
    """
    if ".." in name or "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="Path traversal is not allowed")

    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid filename")

    media_dir = Path(settings.files_dir) / "clip-stacker" / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    dest = media_dir / safe_name
    try:
        if not dest.resolve().is_relative_to(media_dir.resolve()):
            raise HTTPException(status_code=403, detail="Forbidden")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=403, detail="Forbidden")

    content = await file.read()
    dest.write_bytes(content)

    rel_path = f"clip-stacker/media/{safe_name}"
    try:
        await ftp_client.upload(dest, rel_path)
    except Exception as exc:
        logger.warning("FTP upload failed for clip-stacker media (non-fatal): %s", exc)

    base_url = str(settings.static_base_url).rstrip("/")
    public_url = f"{base_url}/{rel_path}"

    return {"url": public_url, "local_path": rel_path, "size_bytes": dest.stat().st_size}


@webhook_router.get("/clip-stacker/media", response_model=dict)
async def clip_stacker_media_list(request: Request, prefix: str = None):
    """List media files in clip-stacker/media directory.

    Query parameter:
    - prefix: Optional prefix to filter files (e.g., a clip ID or project name)

    Response:
    {
        "media": [
            {"name": "filename", "size": 1024, "url": "https://..."},
            ...
        ]
    }
    """
    media_dir = Path(settings.files_dir) / "clip-stacker" / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    media_files = []
    try:
        for file_path in sorted(media_dir.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True):
            if not file_path.is_file():
                continue
            
            filename = file_path.name
            
            # Filter by prefix if provided
            if prefix:
                if not filename.startswith(prefix):
                    continue
            
            size_bytes = file_path.stat().st_size
            base_url = str(settings.static_base_url).rstrip("/")
            public_url = f"{base_url}/clip-stacker/media/{filename}"
            
            media_files.append({
                "name": filename,
                "size": size_bytes,
                "url": public_url,
            })
    except Exception as exc:
        logger.error("Error listing media files: %s", exc)
        raise HTTPException(status_code=500, detail="Error listing media files")

    return {"media": media_files}


@webhook_router.delete("/clip-stacker/media/{filename}", response_model=dict)
async def clip_stacker_media_delete(request: Request, filename: str):
    """Delete a media file from clip-stacker/media.

    Path parameter:
    - filename: The filename to delete (with path-traversal protection)

    Response:
    {
        "status": "success",
        "message": "Media file deleted: {filename}"
    }
    """
    if not filename:
        raise HTTPException(status_code=400, detail="Missing filename")

    # Reject path traversal attempts
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Path traversal is not allowed")

    media_dir = Path(settings.files_dir) / "clip-stacker" / "media"
    media_file = media_dir / filename

    # Ensure the resolved path is within media_dir (prevent traversal)
    try:
        media_file_resolved = media_file.resolve()
        media_dir_resolved = media_dir.resolve()
        if not media_file_resolved.is_relative_to(media_dir_resolved):
            raise HTTPException(status_code=403, detail="Forbidden")
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Unexpected error checking path traversal for media %s: %s", filename, exc)
        raise HTTPException(status_code=403, detail="Forbidden")

    if not media_file.exists():
        raise HTTPException(status_code=404, detail=f"Media file '{filename}' not found")

    try:
        media_file.unlink()
    except Exception as exc:
        logger.error("Failed to delete media file %s: %s", filename, exc)
        raise HTTPException(status_code=500, detail="Failed to delete media file")

    return {"status": "success", "message": f"Media file deleted: {filename}"}


# ====================== Static File Serving ======================
def _file_cors_headers() -> dict:
    """Headers required for cross-origin / COEP clients (mod-player, flac_player)."""
    return {
        "Accept-Ranges": "bytes",
        "Access-Control-Allow-Origin": "*",
        "Cross-Origin-Resource-Policy": "cross-origin",
        "Cache-Control": "public, max-age=3600",
    }


@files_router.head("/{file_path:path}", summary="HEAD for stored files")
async def head_file(file_path: str):
    """Respond to HEAD requests so browsers can check file existence and size."""
    from fastapi.responses import Response as _Response
    base = Path(settings.files_dir).resolve()
    target = (base / file_path).resolve()
    if not target.is_relative_to(base):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    suffix = target.suffix.lower()
    media_type = MIME_MAP.get(suffix, "application/octet-stream")
    headers = {
        "Content-Length": str(target.stat().st_size),
        "Content-Type": media_type,
        **_file_cors_headers(),
    }
    return _Response(status_code=200, headers=headers)


@files_router.get("/{file_path:path}", summary="Serve stored files with correct MIME")
async def serve_file(file_path: str):
    """Serve files from the storage directory with proper MIME types."""
    base = Path(settings.files_dir).resolve()
    target = (base / file_path).resolve()

    # Prevent path traversal
    if not target.is_relative_to(base):
        raise HTTPException(status_code=403, detail="Forbidden")

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")

    suffix = target.suffix.lower()
    media_type = MIME_MAP.get(suffix, "application/octet-stream")

    return FileResponse(target, media_type=media_type, headers=_file_cors_headers())
