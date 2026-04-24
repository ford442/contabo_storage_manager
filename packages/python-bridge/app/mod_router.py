"""MOD music file endpoints — list, metadata, scan, and download from storage."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .config import settings
from .ftp_client import StorageFTPClient

logger = logging.getLogger(__name__)

mod_router = APIRouter(prefix="/api/mods", tags=["mods"])

MOD_EXTENSIONS = frozenset({
    ".mod", ".xm", ".s3m", ".it", ".mptm", ".stm", ".669", ".amf", ".ams",
    ".dbm", ".dmf", ".dsm", ".far", ".gdm", ".j2b", ".mdl", ".med", ".mtm",
    ".okt", ".psm", ".ptm", ".ult", ".umx", ".mt2", ".mo3",
})


# ── Models ─────────────────────────────────────────────────────────────────────

class ModEntry(BaseModel):
    id: str
    filename: str
    title: str = ""
    author: str = ""
    duration: float = 0.0
    size: int = 0
    tags: List[str] = []
    notes: str = ""
    url: str = ""
    added_at: str = ""
    updated_at: str = ""


class ModPatch(BaseModel):
    title: Optional[str] = None
    author: Optional[str] = None
    duration: Optional[float] = None
    tags: Optional[List[str]] = None
    notes: Optional[str] = None


class ScanResult(BaseModel):
    scanned: int
    added: int
    updated: int
    total: int


# ── Helpers ────────────────────────────────────────────────────────────────────

def _mods_dir() -> Path:
    d = Path(settings.files_dir) / "mods"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _index_path() -> Path:
    return _mods_dir() / "index.json"


def _load_index() -> List[dict]:
    p = _index_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else data.get("mods", [])
    except Exception:
        return []


def _save_index(mods: List[dict]) -> None:
    _index_path().write_text(
        json.dumps(mods, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _public_url(filename: str) -> str:
    base = str(settings.static_base_url).rstrip("/")
    return f"{base}/mods/{filename}"


def _file_id(filename: str) -> str:
    """Stable ID derived from filename stem."""
    stem = Path(filename).stem
    return stem.lower().replace(" ", "_")


# ── Endpoints ──────────────────────────────────────────────────────────────────

@mod_router.get("", response_model=List[ModEntry])
async def list_mods(
    search: Optional[str] = None,
    tag: Optional[str] = None,
):
    """List all indexed MOD files, optionally filtered by search or tag."""
    mods = _load_index()

    if search:
        q = search.lower()
        mods = [
            m for m in mods
            if q in m.get("title", "").lower()
            or q in m.get("filename", "").lower()
            or q in m.get("author", "").lower()
        ]
    if tag:
        mods = [m for m in mods if tag in m.get("tags", [])]

    for m in mods:
        m["url"] = _public_url(m["filename"])

    return mods


@mod_router.get("/scan", response_model=ScanResult)
async def scan_mods():
    """Sync MOD files from remote FTP, then scan the mods directory and refresh the index.

    Called by the cloud_notes Sync button to discover new files from storage.
    """
    # Pull new files from the configured FTP/SFTP server first
    try:
        ftp_client = StorageFTPClient()
        ftp_client.sync_mods_from_remote(_mods_dir())
    except Exception as exc:
        logger.error("FTP sync during scan failed: %s", exc)

    mods_dir = _mods_dir()
    now = datetime.now(timezone.utc).isoformat()

    existing: dict = {m["filename"]: m for m in _load_index()}
    added = 0
    updated = 0
    result: List[dict] = []

    files = sorted(
        (p for p in mods_dir.iterdir() if p.suffix.lower() in MOD_EXTENSIONS),
        key=lambda p: p.name.lower(),
    )

    for p in files:
        filename = p.name
        try:
            size = p.stat().st_size
        except OSError:
            size = 0

        if filename in existing:
            entry = dict(existing[filename])
            if entry.get("size") != size:
                entry["size"] = size
                entry["updated_at"] = now
                updated += 1
        else:
            entry = {
                "id": _file_id(filename),
                "filename": filename,
                "title": p.stem,
                "author": "",
                "duration": 0.0,
                "size": size,
                "tags": [],
                "notes": "",
                "added_at": now,
                "updated_at": now,
            }
            added += 1

        entry["url"] = _public_url(filename)
        result.append(entry)

    _save_index(result)
    logger.info("MOD scan complete: %d total, %d added, %d updated", len(result), added, updated)

    return ScanResult(scanned=len(result), added=added, updated=updated, total=len(result))


@mod_router.get("/{mod_id}/download")
async def download_mod(mod_id: str):
    """Stream a MOD file from disk (CORS-safe proxy for the mod-player)."""
    mods = _load_index()
    mod = next(
        (m for m in mods if m.get("id") == mod_id or m.get("filename") == mod_id),
        None,
    )
    if not mod:
        raise HTTPException(status_code=404, detail="MOD not found")

    file_path = _mods_dir() / mod["filename"]
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="MOD file not found on disk")

    return FileResponse(
        file_path,
        media_type="application/octet-stream",
        filename=mod["filename"],
        headers={"Cache-Control": "public, max-age=86400"},
    )


@mod_router.get("/{mod_id}", response_model=ModEntry)
async def get_mod(mod_id: str):
    """Get metadata for a single MOD file by id or filename."""
    mods = _load_index()
    mod = next(
        (m for m in mods if m.get("id") == mod_id or m.get("filename") == mod_id),
        None,
    )
    if not mod:
        raise HTTPException(status_code=404, detail="MOD not found")
    mod["url"] = _public_url(mod["filename"])
    return mod


@mod_router.patch("/{mod_id}", response_model=ModEntry)
async def patch_mod(mod_id: str, patch: ModPatch):
    """Update editable metadata for a MOD file (title, author, tags, notes, duration)."""
    mods = _load_index()
    mod = next(
        (m for m in mods if m.get("id") == mod_id or m.get("filename") == mod_id),
        None,
    )
    if not mod:
        raise HTTPException(status_code=404, detail="MOD not found")

    updates = patch.model_dump(exclude_unset=True)
    for key, value in updates.items():
        if value is not None:
            mod[key] = value
    mod["updated_at"] = datetime.now(timezone.utc).isoformat()

    _save_index(mods)
    mod["url"] = _public_url(mod["filename"])
    return mod
