"""Texture file endpoints — list, serve, upload, and delete image textures.

Texture directories live under ``settings.textures_dir`` (default ``/data/files``):
  textures/        — default texture pool (also used by projectM at runtime)
  weeks_textures/  — textures served from the weeks subdomain
  custom_textures/ — user-uploaded custom textures

Project-M fetches textures from:
  GET /api/textures/                        → directory listing with counts
  GET /api/textures/{dir_name}              → file listing for one dir
  GET /api/textures/{dir_name}/{filename}   → raw image file (FileResponse)
  POST /api/textures/{dir_name}             → upload a texture file (multipart)
  DELETE /api/textures/{dir_name}/{filename} → delete a texture file
"""

import logging
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .config import settings

logger = logging.getLogger(__name__)

textures_router = APIRouter(prefix="/api/textures", tags=["textures"])

# ── Constants ──────────────────────────────────────────────────────────────────

TEXTURE_DIRS: tuple[str, ...] = ("textures", "weeks_textures", "custom_textures")

TEXTURE_EXTENSIONS: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tga"}
)

# MIME type map for supported texture extensions
_MIME_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tga": "image/x-tga",
}

# Static mapping from whitelisted name → filesystem path.
# Built once at module load; callers use this mapping so that the path is never
# derived directly from raw user input.
_TEXTURE_DIR_MAP: dict[str, Path] = {
    name: Path(settings.textures_dir) / name for name in TEXTURE_DIRS
}

# ── Pydantic models ────────────────────────────────────────────────────────────


class TextureDirInfo(BaseModel):
    name: str
    count: int
    updated_at: str | None


class TextureFileMeta(BaseModel):
    name: str
    size: int
    modified_at: str
    url: str  # full URL: {settings.static_base_url}/{dir_name}/{filename}


# ── Helpers ────────────────────────────────────────────────────────────────────


def _validate_dir(dir_name: str) -> None:
    """Raise HTTP 400 if dir_name is not in the allowed whitelist."""
    if dir_name not in TEXTURE_DIRS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown texture directory '{dir_name}'. "
            f"Allowed: {', '.join(TEXTURE_DIRS)}",
        )


def _texture_dir(dir_name: str) -> Path:
    """Return (and auto-create) the path for a whitelisted texture directory.

    Looks up the name in the static ``_TEXTURE_DIR_MAP`` dict so that the
    filesystem path is never constructed from raw user input.
    """
    if dir_name not in _TEXTURE_DIR_MAP:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown texture directory '{dir_name}'. "
            f"Allowed: {', '.join(TEXTURE_DIRS)}",
        )
    d = _TEXTURE_DIR_MAP[dir_name]
    d.mkdir(parents=True, exist_ok=True)
    return d


def _validate_filename(filename: str) -> None:
    """Raise HTTP 400 if the filename is unsafe or not an allowed image type."""
    if not filename:
        raise HTTPException(status_code=400, detail="Filename is required.")
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Path traversal is not allowed.")
    suffix = Path(filename).suffix.lower()
    if suffix not in TEXTURE_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{suffix}'. "
            f"Allowed: {', '.join(sorted(TEXTURE_EXTENSIONS))}",
        )
    # Block control characters and Windows reserved chars to keep filenames safe
    blocked_chars = set(
        "\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f"
        "\x10\x11\x12\x13\x14\x15\x16\x17\x18\x19\x1a\x1b\x1c\x1d\x1e\x1f"
        "\x7f<>:\"|?*"
    )
    if any(c in blocked_chars for c in filename):
        raise HTTPException(
            status_code=400,
            detail="Filename contains invalid characters.",
        )


def _texture_file_path(dir_name: str, filename: str) -> Path:
    """Return the resolved path for a texture file, confined to its directory."""
    texture_dir = _texture_dir(dir_name)
    candidate = (texture_dir / filename).resolve()
    if not candidate.is_relative_to(texture_dir.resolve()):
        raise HTTPException(status_code=400, detail="Path traversal is not allowed.")
    return candidate


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).isoformat()


def _file_url(dir_name: str, filename: str) -> str:
    base = settings.static_base_url.rstrip("/")
    return f"{base}/{dir_name}/{filename}"


# ── Endpoints ──────────────────────────────────────────────────────────────────


@textures_router.get("/", response_model=list[TextureDirInfo])
async def list_texture_dirs():
    """List all texture directories with file counts and latest modification time."""
    results: list[TextureDirInfo] = []
    for name in TEXTURE_DIRS:
        d = _texture_dir(name)
        files = [f for f in d.iterdir() if f.is_file() and f.suffix.lower() in TEXTURE_EXTENSIONS]
        count = len(files)
        updated_at: str | None = None
        if files:
            latest = max(f.stat().st_mtime for f in files)
            updated_at = _iso(latest)
        results.append(TextureDirInfo(name=name, count=count, updated_at=updated_at))
    return results


@textures_router.get("/{dir_name}", response_model=list[TextureFileMeta])
async def list_texture_files(dir_name: str):
    """List all image files in a texture directory, sorted by name."""
    _validate_dir(dir_name)
    d = _texture_dir(dir_name)
    entries: list[TextureFileMeta] = []
    files = sorted(
        (f for f in d.iterdir() if f.is_file() and f.suffix.lower() in TEXTURE_EXTENSIONS),
        key=lambda x: x.name.lower(),
    )
    for p in files:
        stat = p.stat()
        entries.append(
            TextureFileMeta(
                name=p.name,
                size=stat.st_size,
                modified_at=_iso(stat.st_mtime),
                url=_file_url(dir_name, p.name),
            )
        )
    return entries


@textures_router.get("/{dir_name}/{filename}")
async def get_texture_file(dir_name: str, filename: str):
    """Return the raw image file."""
    _validate_dir(dir_name)
    _validate_filename(filename)
    path = _texture_file_path(dir_name, filename)
    if not path.exists():
        raise HTTPException(
            status_code=404, detail=f"Texture '{filename}' not found in '{dir_name}'."
        )
    media_type = _MIME_TYPES.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media_type, filename=filename)


@textures_router.post("/{dir_name}", status_code=201)
async def upload_texture_file(dir_name: str, file: UploadFile):
    """Upload a texture image file to a texture directory (multipart/form-data)."""
    _validate_dir(dir_name)
    filename = file.filename or ""
    _validate_filename(filename)
    path = _texture_file_path(dir_name, filename)
    try:
        content = await file.read()
        path.write_bytes(content)
    except OSError as exc:
        logger.error("Failed to write texture %s/%s: %s", dir_name, filename, exc)
        raise HTTPException(status_code=500, detail="Failed to write texture file.")
    stat = path.stat()
    return {
        "success": True,
        "dir": dir_name,
        "filename": filename,
        "size": stat.st_size,
        "modified_at": _iso(stat.st_mtime),
        "url": _file_url(dir_name, filename),
    }


@textures_router.delete("/{dir_name}/{filename}")
async def delete_texture_file(dir_name: str, filename: str):
    """Delete a texture file."""
    _validate_dir(dir_name)
    _validate_filename(filename)
    path = _texture_file_path(dir_name, filename)
    if not path.exists():
        raise HTTPException(
            status_code=404, detail=f"Texture '{filename}' not found in '{dir_name}'."
        )
    path.unlink()
    return {"success": True, "deleted": filename, "dir": dir_name}
