"""Named note endpoints — plain-text .md files stored under {files_dir}/notes/."""

import json
import logging
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import aiofiles
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .config import settings

logger = logging.getLogger(__name__)

notes_router = APIRouter(prefix="/api/notes", tags=["notes"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-\.]+$")
_ANSIBLE_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$")


def _notes_dir() -> Path:
    return Path(settings.files_dir) / "notes"


def _webhook_dir() -> Path:
    return Path(settings.files_dir) / "notes" / "webhook"


def _markdown_dir() -> Path:
    return Path(settings.files_dir) / "notes" / "markdown"


def _ensure_dirs() -> None:
    """Create all note directories on startup."""
    _notes_dir().mkdir(parents=True, exist_ok=True)
    _webhook_dir().mkdir(parents=True, exist_ok=True)
    _markdown_dir().mkdir(parents=True, exist_ok=True)


def _slugify(title: str) -> str:
    """Convert a human-readable title into a URL-safe filename slug."""
    # Normalize unicode (é -> e)
    text = unicodedata.normalize("NFKD", title)
    # Encode ascii and drop non-ascii chars
    text = text.encode("ascii", "ignore").decode("ascii")
    # Lowercase
    text = text.lower()
    # Replace common separators with spaces
    text = text.replace("_", " ")
    # Remove non-alphanumeric except spaces and hyphens
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    # Collapse whitespace
    text = re.sub(r"\s+", "-", text).strip("-")
    # Collapse multiple hyphens
    text = re.sub(r"-+", "-", text)
    # Limit length
    if len(text) > 60:
        text = text[:60].rsplit("-", 1)[0]
    # Fallback
    if not text:
        text = "untitled"
    return text


def _validate_name(note_name: str) -> None:
    """Raise HTTP 400 if note_name contains unsafe characters."""
    if not note_name:
        raise HTTPException(status_code=400, detail="Note name is required.")
    if not _SAFE_NAME_RE.match(note_name):
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid note name. Only alphanumeric characters, underscores, "
                "hyphens, and dots are allowed."
            ),
        )
    # Belt-and-suspenders: reject traversal sequences even if the regex passed
    if ".." in note_name or "/" in note_name or "\\" in note_name:
        raise HTTPException(status_code=400, detail="Path traversal is not allowed.")


def _note_path(note_name: str) -> Path:
    """Return the resolved path for a note, confined to the notes directory."""
    notes_dir = _notes_dir()
    candidate = (notes_dir / f"{note_name}.md").resolve()
    # Confine to the notes directory to prevent any residual traversal
    resolved_notes = str(notes_dir.resolve())
    resolved_candidate = str(candidate)
    separator = "/"
    if not resolved_candidate.startswith(resolved_notes + separator) and resolved_candidate != resolved_notes:
        raise HTTPException(status_code=400, detail="Path traversal is not allowed.")
    return candidate


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class WriteNoteRequest(BaseModel):
    content: str


class SaveNoteRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    content: str = ""
    tags: str = ""


class SyncNotePayload(BaseModel):
    source: str = "cloud_notes"
    event: str = "note.updated"
    timestamp: str = Field(default_factory=_now_iso)
    data: dict


class BatchSyncPayload(BaseModel):
    notes: list[SyncNotePayload]


class NoteEntry(BaseModel):
    name: str
    title: str = ""
    updated_at: str
    size: int


class NoteContent(BaseModel):
    name: str
    content: str
    updated_at: str


class SaveNoteResponse(BaseModel):
    success: bool
    name: str
    title: str
    size: int
    updated_at: str


# Ensure directories exist when module loads
_ensure_dirs()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@notes_router.get("/list", response_model=list[NoteEntry])
async def list_notes():
    """List all notes, sorted by last-modified descending."""
    notes_dir = _notes_dir()
    notes_dir.mkdir(parents=True, exist_ok=True)
    if not notes_dir.exists():
        return []

    entries: list[tuple[float, NoteEntry]] = []
    for path in notes_dir.glob("*.md"):
        stat = path.stat()
        entries.append(
            (
                stat.st_mtime,
                NoteEntry(
                    name=path.stem,
                    updated_at=_iso(stat.st_mtime),
                    size=stat.st_size,
                ),
            )
        )

    entries.sort(key=lambda t: t[0], reverse=True)
    return [e for _, e in entries]


@notes_router.get("/read/{note_name}", response_model=NoteContent)
async def read_note(note_name: str):
    """Return the content of a note by name (no extension needed)."""
    _validate_name(note_name)
    path = _note_path(note_name)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Note '{note_name}' not found.")

    stat = path.stat()
    try:
        async with aiofiles.open(path, encoding="utf-8") as f:
            content = await f.read()
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=422,
            detail=f"Note '{note_name}' contains invalid UTF-8 encoding.",
        )
    return NoteContent(
        name=note_name,
        content=content,
        updated_at=_iso(stat.st_mtime),
    )


@notes_router.post("/write/{note_name}")
async def write_note(note_name: str, body: WriteNoteRequest):
    """Create or overwrite a note by name."""
    note_name = _slugify(note_name)
    _validate_name(note_name)
    notes_dir = _notes_dir()
    notes_dir.mkdir(parents=True, exist_ok=True)

    path = _note_path(note_name)
    async with aiofiles.open(path, "w", encoding="utf-8") as f:
        await f.write(body.content)

    stat = path.stat()
    return {
        "success": True,
        "name": note_name,
        "size": stat.st_size,
        "updated_at": _iso(stat.st_mtime),
    }


@notes_router.post("/save", response_model=SaveNoteResponse)
async def save_note(body: SaveNoteRequest):
    """Create or overwrite a note using a human-readable title.
    
    The title is automatically slugified for the filename.
    """
    slug = _slugify(body.title)
    notes_dir = _notes_dir()
    notes_dir.mkdir(parents=True, exist_ok=True)

    path = _note_path(slug)
    async with aiofiles.open(path, "w", encoding="utf-8") as f:
        await f.write(body.content)

    stat = path.stat()
    return SaveNoteResponse(
        success=True,
        name=slug,
        title=body.title,
        size=stat.st_size,
        updated_at=_iso(stat.st_mtime),
    )


@notes_router.post("/sync")
async def sync_note(payload: SyncNotePayload):
    """Receive a cloud_notes-style payload and save it as a markdown note.
    
    This endpoint does NOT require HMAC signature, making it ideal for
    direct browser-to-server sync from the cloud_notes app.
    """
    data = payload.data
    note_id = data.get("id") or _now_iso()
    title = data.get("title", "Untitled")
    content = data.get("content", "")
    updated_at = data.get("updatedAt") or _now_iso()

    slug = _slugify(title)
    notes_dir = _notes_dir()
    notes_dir.mkdir(parents=True, exist_ok=True)

    path = _note_path(slug)

    # Build markdown with frontmatter
    md_output = f"""---
id: {note_id}
title: {title}
updatedAt: {updated_at}
---

{content}
"""
    async with aiofiles.open(path, "w", encoding="utf-8") as f:
        await f.write(md_output)

    # Also save the raw JSON payload for archival
    webhook_dir = _webhook_dir()
    webhook_dir.mkdir(parents=True, exist_ok=True)
    safe_title = "".join(c if c.isalnum() or c in "._-" else "_" for c in title)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    json_path = webhook_dir / f"{timestamp}_{safe_title}.json"
    async with aiofiles.open(json_path, "w", encoding="utf-8") as f:
        await f.write(json.dumps({
            "id": note_id,
            "title": title,
            "content": content,
            "updatedAt": updated_at,
            "source": payload.source,
            "event": payload.event,
            "receivedAt": _now_iso(),
        }, indent=2))

    stat = path.stat()
    return {
        "success": True,
        "name": slug,
        "title": title,
        "size": stat.st_size,
        "updated_at": _iso(stat.st_mtime),
        "archived": str(json_path.relative_to(Path(settings.files_dir))),
    }


@notes_router.post("/sync/batch")
async def sync_notes_batch(payload: BatchSyncPayload):
    """Sync multiple notes in a single request."""
    results = []
    errors = []

    for note_payload in payload.notes:
        try:
            result = await sync_note(note_payload)
            results.append(result)
        except Exception as exc:
            title = note_payload.data.get("title", "unknown")
            errors.append({"title": title, "error": str(exc)})
            logger.warning("Batch sync failed for note %s: %s", title, exc)

    return {
        "success": len(errors) == 0,
        "saved": len(results),
        "failed": len(errors),
        "results": results,
        "errors": errors,
    }


@notes_router.delete("/delete/{note_name}")
async def delete_note(note_name: str):
    """Delete a note by name."""
    _validate_name(note_name)
    path = _note_path(note_name)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Note '{note_name}' not found.")

    path.unlink()
    return {"success": True, "deleted": note_name}
