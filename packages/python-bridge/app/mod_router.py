from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import json
import logging
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .config import settings
from .ftp_client import StorageFTPClient

logger = logging.getLogger(__name__)

mod_router = APIRouter(prefix="/api/mods", tags=["mods"])

MOD_EXTENSIONS = frozenset({
    ".mod", ".xm", ".s3m", ".it", ".mptm", ".stm", ".669", ".amf", ".ams",
    ".dbm", ".dmf", ".dsm", ".far", ".gdm", ".j2b", ".mdl", ".med", ".mtm",
    ".okt", ".psm", ".ptm", ".ult", ".umx", ".mt2", ".mo3",
})


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


def _mods_dir() -> Path:
    from app.config import get_settings
    settings = get_settings()
    mods_path = Path(settings.files_dir) / "mods"
    mods_path.mkdir(parents=True, exist_ok=True)
    return mods_path


def _index_path() -> Path:
    return _mods_dir() / "index.json"


def _load_index() -> dict:
    index_path = _index_path()
    if index_path.exists():
        try:
            with open(index_path, 'r') as f:
                data = json.load(f)
            # Migrate legacy list format to dict keyed by id
            if isinstance(data, list):
                return {item["id"]: item for item in data if "id" in item}
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def _save_index(index: dict) -> None:
    index_path = _index_path()
    with open(index_path, 'w') as f:
        json.dump(index, f, indent=2)


def _public_url(filename: str) -> str:
    from app.config import get_settings
    settings = get_settings()
    return f"{settings.static_base_url}/mods/{filename}"


def _file_id(filename: str) -> str:
    return Path(filename).stem.lower().replace(" ", "_")


def _parse_duration(value: str) -> float:
    """Parse openmpt123 duration strings like '00:07.680' or '01:02:03.456'."""
    value = value.strip()
    parts = value.split(":")
    try:
        if len(parts) == 2:
            # MM:SS.mmm
            minutes = float(parts[0])
            seconds = float(parts[1])
            return minutes * 60 + seconds
        elif len(parts) == 3:
            # HH:MM:SS.mmm
            hours = float(parts[0])
            minutes = float(parts[1])
            seconds = float(parts[2])
            return hours * 3600 + minutes * 60 + seconds
    except ValueError:
        pass
    return 0.0


def _extract_mod_metadata(filepath: Path) -> dict:
    """Run openmpt123 --info and extract Title, Tracker (author), and Duration."""
    result = {
        "title": "",
        "author": "",
        "duration": 0.0,
    }
    try:
        proc = subprocess.run(
            ["openmpt123", "--info", str(filepath)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0 and not proc.stdout:
            logger.warning("openmpt123 failed for %s: %s", filepath.name, proc.stderr)
            return result

        for line in proc.stdout.splitlines():
            if ":" not in line:
                continue
            # Lines look like:  "Title......: Some Song Name"
            key_part, _, value = line.partition(":")
            key = key_part.strip(". ").lower()
            value = value.strip()

            if key == "title":
                result["title"] = value
            elif key == "tracker":
                result["author"] = value
            elif key == "duration":
                result["duration"] = _parse_duration(value)

    except subprocess.TimeoutExpired:
        logger.warning("openmpt123 timed out for %s", filepath.name)
    except FileNotFoundError:
        logger.error("openmpt123 not found. Install it with: sudo apt-get install openmpt123")
    except Exception as exc:
        logger.warning("Error extracting metadata from %s: %s", filepath.name, exc)

    return result


@mod_router.get("", response_model=List[ModEntry])
async def list_mods(search: Optional[str] = None, tag: Optional[str] = None):
    """List all MOD files with metadata."""
    index = _load_index()
    entries = [ModEntry(**data) for data in index.values()]

    if search:
        search_lower = search.lower()
        entries = [e for e in entries if search_lower in e.title.lower() or search_lower in e.author.lower()]

    if tag:
        entries = [e for e in entries if tag in e.tags]

    return entries


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
    index = _load_index()

    scanned = 0
    added = 0
    updated = 0

    now = datetime.now(timezone.utc).isoformat()

    for filepath in mods_dir.iterdir():
        if not filepath.is_file():
            continue

        ext = filepath.suffix.lower()
        if ext not in MOD_EXTENSIONS:
            continue

        scanned += 1
        filename = filepath.name
        file_id = _file_id(filename)
        size = filepath.stat().st_size

        # Extract metadata via openmpt123
        meta = _extract_mod_metadata(filepath)

        if file_id not in index:
            index[file_id] = {
                "id": file_id,
                "filename": filename,
                "title": meta["title"] or Path(filename).stem,
                "author": meta["author"],
                "duration": meta["duration"],
                "size": size,
                "tags": [],
                "notes": "",
                "url": _public_url(filename),
                "added_at": now,
                "updated_at": now
            }
            added += 1
        else:
            # Update size and timestamp, preserve user metadata
            entry = index[file_id]
            entry["size"] = size
            entry["updated_at"] = now
            # Backfill metadata if missing
            if not entry.get("title") or entry.get("title") == Path(filename).stem:
                entry["title"] = meta["title"] or Path(filename).stem
            if not entry.get("author"):
                entry["author"] = meta["author"]
            if entry.get("duration", 0.0) == 0.0 and meta["duration"] > 0:
                entry["duration"] = meta["duration"]
            updated += 1

    _save_index(index)

    return ScanResult(
        scanned=scanned,
        added=added,
        updated=updated,
        total=len(index)
    )


@mod_router.post("/reindex", response_model=ScanResult)
async def reindex_mods():
    """Re-extract metadata for all existing indexed mods using openmpt123.

    Use this to backfill titles/authors/durations after installing openmpt123
    or when files have been updated with new metadata.
    """
    mods_dir = _mods_dir()
    index = _load_index()

    scanned = 0
    updated = 0

    now = datetime.now(timezone.utc).isoformat()

    for file_id, entry in list(index.items()):
        filepath = mods_dir / entry.get("filename", "")
        if not filepath.exists() or not filepath.is_file():
            continue

        ext = filepath.suffix.lower()
        if ext not in MOD_EXTENSIONS:
            continue

        scanned += 1
        meta = _extract_mod_metadata(filepath)

        changed = False
        if meta["title"]:
            entry["title"] = meta["title"]
            changed = True
        if meta["author"]:
            entry["author"] = meta["author"]
            changed = True
        if meta["duration"] > 0:
            entry["duration"] = meta["duration"]
            changed = True

        if changed:
            entry["updated_at"] = now
            updated += 1

    _save_index(index)

    return ScanResult(
        scanned=scanned,
        added=0,
        updated=updated,
        total=len(index)
    )


@mod_router.get("/{mod_id}/download")
async def download_mod(mod_id: str):
    """CORS-safe binary download proxy for MOD files."""
    from fastapi.responses import FileResponse

    index = _load_index()
    if mod_id not in index:
        raise HTTPException(status_code=404, detail="MOD not found")

    entry = index[mod_id]
    mods_dir = _mods_dir()
    filepath = mods_dir / entry["filename"]

    if not filepath.exists():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(
        path=filepath,
        media_type="application/octet-stream",
        filename=entry["filename"]
    )


@mod_router.get("/{mod_id}", response_model=ModEntry)
async def get_mod(mod_id: str):
    """Get metadata for a specific MOD file."""
    index = _load_index()
    if mod_id not in index:
        raise HTTPException(status_code=404, detail="MOD not found")

    return ModEntry(**index[mod_id])


@mod_router.patch("/{mod_id}", response_model=ModEntry)
async def patch_mod(mod_id: str, patch: ModPatch):
    """Update metadata for a MOD file."""
    index = _load_index()
    if mod_id not in index:
        raise HTTPException(status_code=404, detail="MOD not found")

    entry = index[mod_id]
    data = patch.model_dump(exclude_unset=True)
    entry.update(data)

    entry["updated_at"] = datetime.now(timezone.utc).isoformat()

    _save_index(index)

    return ModEntry(**entry)
