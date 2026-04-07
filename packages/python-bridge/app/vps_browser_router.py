"""VPS File Browser API — browse, download, upload, save and delete files
stored under settings.files_dir on the Contabo VPS.

Security: all path parameters are resolved against settings.files_dir and any
path that escapes that root is rejected with HTTP 400.
"""

import mimetypes
import os
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form, Body
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from .config import settings

vps_browser_router = APIRouter(prefix="/api/vps", tags=["vps-browser"])


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class VFSEntry(BaseModel):
    name: str
    path: str        # relative to files_dir, using forward slashes
    type: str        # "file" | "dir"
    size: int        # bytes (0 for dirs)
    modified: float  # unix timestamp
    mime: str        # guessed MIME type, empty string for dirs


class SaveFileRequest(BaseModel):
    path: str
    content: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve(rel_path: str) -> Path:
    """Resolve a relative path safely inside files_dir.

    Raises HTTP 400 if the resolved path escapes the root.
    """
    base = Path(settings.files_dir).resolve()
    # Strip leading slashes so Path doesn't treat it as absolute
    clean = rel_path.lstrip("/").replace("..", "")
    resolved = (base / clean).resolve()
    if not str(resolved).startswith(str(base)):
        raise HTTPException(status_code=400, detail="Invalid path: outside storage root")
    return resolved


def _rel(abs_path: Path) -> str:
    """Return the path relative to files_dir using forward slashes."""
    base = Path(settings.files_dir).resolve()
    return str(abs_path.relative_to(base)).replace(os.sep, "/")


def _mime(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "application/octet-stream"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@vps_browser_router.get("/browse", response_model=List[VFSEntry])
async def browse(path: str = Query(default="", description="Relative path inside files_dir")):
    """List the contents of a directory on the VPS."""
    target = _resolve(path)
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {path}")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {path}")

    entries: List[VFSEntry] = []
    try:
        for item in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            stat = item.stat()
            entries.append(VFSEntry(
                name=item.name,
                path=_rel(item),
                type="file" if item.is_file() else "dir",
                size=stat.st_size if item.is_file() else 0,
                modified=stat.st_mtime,
                mime=_mime(item) if item.is_file() else "",
            ))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    return entries


@vps_browser_router.get("/file")
async def get_file(path: str = Query(..., description="Relative file path inside files_dir")):
    """Download / stream a file from the VPS."""
    target = _resolve(path)
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    if not target.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {path}")

    return FileResponse(
        path=str(target),
        media_type=_mime(target),
        filename=target.name,
    )


@vps_browser_router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    path: str = Form(default="", description="Target directory relative to files_dir"),
):
    """Upload a file to a directory on the VPS."""
    target_dir = _resolve(path)
    target_dir.mkdir(parents=True, exist_ok=True)

    if not target_dir.is_dir():
        raise HTTPException(status_code=400, detail=f"Target is not a directory: {path}")

    filename = file.filename or "upload"
    dest = target_dir / Path(filename).name  # strip any directory component in filename

    content = await file.read()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail=f"File exceeds {settings.max_upload_mb} MB limit")

    dest.write_bytes(content)
    stat = dest.stat()

    return {
        "success": True,
        "path": _rel(dest),
        "name": dest.name,
        "size": stat.st_size,
        "mime": _mime(dest),
    }


@vps_browser_router.put("/file")
async def save_file(body: SaveFileRequest):
    """Overwrite an existing file with new text content (for saving edits)."""
    target = _resolve(body.path)

    # Only allow overwriting existing files via PUT; use POST /upload for new files
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {body.path}. Use POST /upload to create new files.")
    if not target.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {body.path}")

    target.write_text(body.content, encoding="utf-8")
    stat = target.stat()

    return {
        "success": True,
        "path": _rel(target),
        "size": stat.st_size,
    }


@vps_browser_router.delete("/file")
async def delete_file(path: str = Query(..., description="Relative file path inside files_dir")):
    """Delete a file from the VPS."""
    target = _resolve(path)
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Not found: {path}")
    if not target.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {path}. Use /rmdir to remove directories.")

    target.unlink()
    return {"success": True, "deleted": path}
