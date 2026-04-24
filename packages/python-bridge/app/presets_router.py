"""MilkDrop preset endpoints — list, read, upload, and delete .milk files.

Preset directories live under ``settings.presets_dir`` (default ``/data/files``):
  milk/        — default preset pool
  milkSML/     — small preset pool
  milkMED/     — medium preset pool
  milkLRG/     — large preset pool
  custom_milk/ — user-uploaded custom presets

Project-M fetches presets from:
  GET /api/presets/               → directory listing with counts
  GET /api/presets/{dir_name}     → .milk file listing for one dir
  GET /api/presets/{dir_name}/{filename}  → raw preset content (text/plain)
"""

import logging
from datetime import UTC, datetime
from pathlib import Path

import aiofiles
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from .config import settings

logger = logging.getLogger(__name__)

presets_router = APIRouter(prefix="/api/presets", tags=["presets"])

# ── Constants ──────────────────────────────────────────────────────────────────

PRESET_DIRS: tuple[str, ...] = ("milk", "milkSML", "milkMED", "milkLRG", "custom_milk")
MILK_SUFFIX = ".milk"

# Static mapping from whitelisted name → filesystem path.
# Built once at module load; callers use this mapping so that the path is never
# derived directly from raw user input.
_PRESET_DIR_MAP: dict[str, Path] = {
    name: Path(settings.presets_dir) / name for name in PRESET_DIRS
}

# ── Pydantic models ────────────────────────────────────────────────────────────


class PresetDirInfo(BaseModel):
    name: str
    count: int
    updated_at: str | None


class PresetFileMeta(BaseModel):
    name: str
    size: int
    modified_at: str


class SavePresetRequest(BaseModel):
    filename: str
    content: str


# ── Helpers ────────────────────────────────────────────────────────────────────


def _validate_dir(dir_name: str) -> None:
    """Raise HTTP 400 if dir_name is not in the allowed whitelist."""
    if dir_name not in PRESET_DIRS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown preset directory '{dir_name}'. "
            f"Allowed: {', '.join(PRESET_DIRS)}",
        )


def _preset_dir(dir_name: str) -> Path:
    """Return (and auto-create) the path for a whitelisted preset directory.

    Looks up the name in the static ``_PRESET_DIR_MAP`` dict so that the
    filesystem path is never constructed from raw user input.
    """
    if dir_name not in _PRESET_DIR_MAP:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown preset directory '{dir_name}'. "
            f"Allowed: {', '.join(PRESET_DIRS)}",
        )
    d = _PRESET_DIR_MAP[dir_name]
    d.mkdir(parents=True, exist_ok=True)
    return d


def _validate_filename(filename: str) -> None:
    """Raise HTTP 400 if the filename is unsafe or not a .milk file."""
    if not filename:
        raise HTTPException(status_code=400, detail="Filename is required.")
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Path traversal is not allowed.")
    if not filename.lower().endswith(MILK_SUFFIX):
        raise HTTPException(
            status_code=400, detail=f"Only {MILK_SUFFIX} files are supported."
        )
    # Restrict to safe characters: alphanumeric, spaces, dots, hyphens, underscores, parens
    safe_chars = set(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ._-+()"
    )
    if not all(c in safe_chars for c in filename):
        raise HTTPException(
            status_code=400,
            detail="Filename contains invalid characters. Use alphanumeric, spaces, and ._-+() only.",
        )


def _preset_file_path(dir_name: str, filename: str) -> Path:
    """Return the resolved path for a preset file, confined to its directory."""
    preset_dir = _preset_dir(dir_name)
    candidate = (preset_dir / filename).resolve()
    if not candidate.is_relative_to(preset_dir.resolve()):
        raise HTTPException(status_code=400, detail="Path traversal is not allowed.")
    return candidate


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).isoformat()


# ── Endpoints ──────────────────────────────────────────────────────────────────


@presets_router.get("/", response_model=list[PresetDirInfo])
async def list_preset_dirs():
    """List all preset directories with file counts and latest modification time."""
    results: list[PresetDirInfo] = []
    for name in PRESET_DIRS:
        d = _preset_dir(name)
        files = list(d.glob(f"*{MILK_SUFFIX}"))
        count = len(files)
        updated_at: str | None = None
        if files:
            latest = max(f.stat().st_mtime for f in files)
            updated_at = _iso(latest)
        results.append(PresetDirInfo(name=name, count=count, updated_at=updated_at))
    return results


@presets_router.get("/{dir_name}", response_model=list[PresetFileMeta])
async def list_preset_files(dir_name: str):
    """List all .milk files in a preset directory, sorted by name."""
    _validate_dir(dir_name)
    d = _preset_dir(dir_name)
    entries: list[PresetFileMeta] = []
    for p in sorted(d.glob(f"*{MILK_SUFFIX}"), key=lambda x: x.name.lower()):
        stat = p.stat()
        entries.append(
            PresetFileMeta(
                name=p.name,
                size=stat.st_size,
                modified_at=_iso(stat.st_mtime),
            )
        )
    return entries


@presets_router.get("/{dir_name}/{filename}")
async def get_preset_file(dir_name: str, filename: str):
    """Return the raw content of a .milk preset file as text/plain."""
    _validate_dir(dir_name)
    _validate_filename(filename)
    path = _preset_file_path(dir_name, filename)
    if not path.exists():
        raise HTTPException(
            status_code=404, detail=f"Preset '{filename}' not found in '{dir_name}'."
        )
    try:
        async with aiofiles.open(path, encoding="utf-8", errors="replace") as f:
            content = await f.read()
    except OSError as exc:
        logger.error("Failed to read preset %s/%s: %s", dir_name, filename, exc)
        raise HTTPException(status_code=500, detail="Failed to read preset file.")
    return PlainTextResponse(content=content)


@presets_router.post("/{dir_name}", status_code=201)
async def save_preset_file(dir_name: str, body: SavePresetRequest):
    """Upload or overwrite a .milk file in a preset directory.

    Request body: ``{ "filename": "example.milk", "content": "..." }``
    """
    _validate_dir(dir_name)
    _validate_filename(body.filename)
    path = _preset_file_path(dir_name, body.filename)
    try:
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(body.content)
    except OSError as exc:
        logger.error("Failed to write preset %s/%s: %s", dir_name, body.filename, exc)
        raise HTTPException(status_code=500, detail="Failed to write preset file.")
    stat = path.stat()
    return {
        "success": True,
        "dir": dir_name,
        "filename": body.filename,
        "size": stat.st_size,
        "modified_at": _iso(stat.st_mtime),
    }


@presets_router.delete("/{dir_name}/{filename}")
async def delete_preset_file(dir_name: str, filename: str):
    """Delete a .milk preset file."""
    _validate_dir(dir_name)
    _validate_filename(filename)
    path = _preset_file_path(dir_name, filename)
    if not path.exists():
        raise HTTPException(
            status_code=404, detail=f"Preset '{filename}' not found in '{dir_name}'."
        )
    path.unlink()
    return {"success": True, "deleted": filename, "dir": dir_name}
