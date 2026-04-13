"""Named note endpoints — plain-text .md files stored under {files_dir}/notes/."""

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .config import settings

logger = logging.getLogger(__name__)

notes_router = APIRouter(prefix="/api/notes", tags=["notes"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-\.]+$")


def _notes_dir() -> Path:
    return Path(settings.files_dir) / "notes"


def _validate_name(note_name: str) -> None:
    """Raise HTTP 400 if note_name contains unsafe characters."""
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
    if not str(candidate).startswith(str(notes_dir.resolve()) + "/") and str(candidate) != str(notes_dir.resolve()):
        raise HTTPException(status_code=400, detail="Path traversal is not allowed.")
    return candidate


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class WriteNoteRequest(BaseModel):
    content: str


class NoteEntry(BaseModel):
    name: str
    updated_at: str
    size: int


class NoteContent(BaseModel):
    name: str
    content: str
    updated_at: str


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
        content = path.read_text(encoding="utf-8")
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
    _validate_name(note_name)
    notes_dir = _notes_dir()
    notes_dir.mkdir(parents=True, exist_ok=True)

    path = _note_path(note_name)
    path.write_text(body.content, encoding="utf-8")

    stat = path.stat()
    return {
        "success": True,
        "name": note_name,
        "size": stat.st_size,
        "updated_at": _iso(stat.st_mtime),
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
