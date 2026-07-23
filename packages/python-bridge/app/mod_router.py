from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import List, Optional
import asyncio
import json
import logging
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

from .config import settings

logger = logging.getLogger(__name__)

mod_router = APIRouter(prefix="/api/mods", tags=["mods"])

MOD_EXTENSIONS = frozenset({
    ".mod", ".xm", ".s3m", ".it", ".mptm", ".stm", ".669", ".amf", ".ams",
    ".dbm", ".dmf", ".dsm", ".far", ".gdm", ".j2b", ".mdl", ".med", ".mtm",
    ".okt", ".psm", ".ptm", ".ult", ".umx", ".mt2", ".mo3",
})

# In-memory index cache (per worker). Invalidated when index.json mtime changes
# or after a scan/reindex/patch write.
_index_lock = threading.Lock()
_index_cache: dict | None = None
_index_mtime: float | None = None


class ModEntry(BaseModel):
    id: str
    filename: str
    title: str = ""
    author: str = ""
    duration: float = 0.0
    size: int = 0
    tags: List[str] = Field(default_factory=list)
    notes: str = ""
    url: str = ""
    # CORS-safe proxy preferred by COEP/COOP clients (mod-player)
    download_url: str = ""
    downloadUrl: str = ""  # camelCase alias for browser clients
    added_at: str = ""
    updated_at: str = ""
    # SongMetadata-compatible fields so /api/mods can feed generic library UIs
    name: str = ""
    type: str = "mod"
    artist: str = ""
    durationSeconds: Optional[float] = None
    fileName: str = ""


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
    message: Optional[str] = None


def _get_settings():
    from .config import get_settings
    return get_settings()


def _mods_dir() -> Path:
    cfg = _get_settings()
    mods_path = Path(cfg.files_dir) / "mods"
    mods_path.mkdir(parents=True, exist_ok=True)
    return mods_path


def _index_path() -> Path:
    return _mods_dir() / "index.json"


def _api_base_url() -> str:
    """Public origin without the /files suffix, e.g. https://storage.noahcohn.com."""
    cfg = _get_settings()
    base = str(cfg.static_base_url).rstrip("/")
    if base.endswith("/files"):
        return base[: -len("/files")]
    return base


def _static_base_url() -> str:
    """Public static root including /files when configured."""
    cfg = _get_settings()
    return str(cfg.static_base_url).rstrip("/")


def _public_url(filename: str) -> str:
    """Direct static URL under STATIC_BASE_URL/mods/… (path-segment encoded)."""
    from urllib.parse import quote
    return f"{_static_base_url()}/mods/{quote(filename)}"


def _download_proxy_url(mod_id: str) -> str:
    """CORS-safe download proxy used by browser clients with COEP."""
    return f"{_api_base_url()}/api/mods/{mod_id}/download"


def _file_id(filename: str) -> str:
    return Path(filename).stem.lower().replace(" ", "_")


def _invalidate_index_cache() -> None:
    global _index_cache, _index_mtime
    with _index_lock:
        _index_cache = None
        _index_mtime = None


def _load_index() -> dict:
    """Load mods index with mtime-based in-memory cache."""
    global _index_cache, _index_mtime
    index_path = _index_path()

    try:
        mtime = index_path.stat().st_mtime if index_path.exists() else None
    except OSError:
        mtime = None

    with _index_lock:
        if _index_cache is not None and mtime is not None and mtime == _index_mtime:
            return dict(_index_cache)

    data: dict = {}
    if index_path.exists():
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, list):
                data = {item["id"]: item for item in raw if isinstance(item, dict) and "id" in item}
            elif isinstance(raw, dict):
                data = raw
        except Exception as exc:
            logger.warning("Failed to load mods index: %s", exc)
            data = {}

    with _index_lock:
        _index_cache = dict(data)
        _index_mtime = mtime
    return data


def _save_index(index: dict) -> None:
    index_path = _index_path()
    temp_path = index_path.with_suffix(".json.tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2)
    temp_path.replace(index_path)
    _invalidate_index_cache()


def _parse_duration(value: str) -> float:
    """Parse openmpt123 duration strings like '00:07.680' or '01:02:03.456'."""
    value = value.strip()
    parts = value.split(":")
    try:
        if len(parts) == 2:
            minutes = float(parts[0])
            seconds = float(parts[1])
            return minutes * 60 + seconds
        elif len(parts) == 3:
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


def _enrich_entry(entry: dict) -> dict:
    """Ensure URLs and library-compatible aliases are current."""
    filename = entry.get("filename") or ""
    mod_id = entry.get("id") or _file_id(filename)
    entry["id"] = mod_id
    entry["filename"] = filename
    # Always rewrite public URLs to current STATIC_BASE_URL (fixes stale 1ink.us links)
    if filename:
        entry["url"] = _public_url(filename)
    proxy = _download_proxy_url(mod_id)
    entry["download_url"] = proxy
    entry["downloadUrl"] = proxy

    title = entry.get("title") or Path(filename).stem if filename else mod_id
    author = entry.get("author") or ""
    duration = float(entry.get("duration") or 0.0)

    entry.setdefault("title", title)
    entry.setdefault("author", author)
    entry.setdefault("duration", duration)
    entry.setdefault("size", int(entry.get("size") or 0))
    entry.setdefault("tags", entry.get("tags") or [])
    entry.setdefault("notes", entry.get("notes") or "")
    entry.setdefault("added_at", entry.get("added_at") or "")
    entry.setdefault("updated_at", entry.get("updated_at") or "")

    # Aliases for generic library clients (mod-player RemoteSong normalizer)
    entry["name"] = title
    entry["fileName"] = filename
    entry["artist"] = author
    entry["type"] = "mod"
    entry["durationSeconds"] = duration if duration > 0 else None
    return entry


def _entry_to_model(entry: dict) -> ModEntry:
    return ModEntry(**_enrich_entry(dict(entry)))


def _quick_index_from_disk() -> dict:
    """Build a minimal index from files on disk without openmpt123.

    Used when the index is empty but MOD files exist so library listing works
    immediately. Full metadata can be filled later via /scan or /reindex.
    """
    mods_dir = _mods_dir()
    index: dict = {}
    now = datetime.now(timezone.utc).isoformat()
    try:
        entries = list(mods_dir.iterdir())
    except OSError as exc:
        logger.error("Cannot list mods dir: %s", exc)
        return index

    for filepath in entries:
        if not filepath.is_file():
            continue
        if filepath.suffix.lower() not in MOD_EXTENSIONS:
            continue
        filename = filepath.name
        file_id = _file_id(filename)
        try:
            size = filepath.stat().st_size
        except OSError:
            size = 0
        index[file_id] = {
            "id": file_id,
            "filename": filename,
            "title": Path(filename).stem,
            "author": "",
            "duration": 0.0,
            "size": size,
            "tags": [],
            "notes": "",
            "url": _public_url(filename),
            "added_at": now,
            "updated_at": now,
        }
    return index


def _ensure_index_populated() -> dict:
    """Return index, auto-building a lightweight one if empty but files exist."""
    index = _load_index()
    if index:
        return index

    quick = _quick_index_from_disk()
    if quick:
        logger.info("Mods index was empty; auto-indexed %d files from disk", len(quick))
        _save_index(quick)
        return quick
    return index


def _scan_mods_sync(pull_remote: bool = True, extract_metadata: bool = True) -> ScanResult:
    """Blocking scan implementation (run in a thread from async handlers)."""
    if pull_remote:
        try:
            from .ftp_client import StorageFTPClient
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

    try:
        filepaths = list(mods_dir.iterdir())
    except OSError as exc:
        logger.error("Cannot list mods dir during scan: %s", exc)
        return ScanResult(scanned=0, added=0, updated=0, total=len(index), message=str(exc))

    for filepath in filepaths:
        if not filepath.is_file():
            continue
        ext = filepath.suffix.lower()
        if ext not in MOD_EXTENSIONS:
            continue

        scanned += 1
        filename = filepath.name
        file_id = _file_id(filename)
        try:
            size = filepath.stat().st_size
        except OSError:
            size = 0

        existing = index.get(file_id)
        # Skip expensive openmpt extraction when size is unchanged and metadata exists
        need_meta = extract_metadata and (
            existing is None
            or not existing.get("title")
            or existing.get("title") == Path(filename).stem
            or not existing.get("author")
            or float(existing.get("duration") or 0) == 0.0
            or int(existing.get("size") or 0) != size
        )
        meta = _extract_mod_metadata(filepath) if need_meta else {
            "title": (existing or {}).get("title") or "",
            "author": (existing or {}).get("author") or "",
            "duration": float((existing or {}).get("duration") or 0.0),
        }

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
                "updated_at": now,
            }
            added += 1
        else:
            entry = index[file_id]
            entry["filename"] = filename
            entry["size"] = size
            entry["url"] = _public_url(filename)
            entry["updated_at"] = now
            if not entry.get("title") or entry.get("title") == Path(filename).stem:
                if meta["title"]:
                    entry["title"] = meta["title"]
            if not entry.get("author") and meta["author"]:
                entry["author"] = meta["author"]
            if float(entry.get("duration") or 0) == 0.0 and meta["duration"] > 0:
                entry["duration"] = meta["duration"]
            updated += 1

    # Drop index entries whose files no longer exist
    stale_ids = []
    for file_id, entry in index.items():
        filename = entry.get("filename") or ""
        if not filename or not (mods_dir / filename).is_file():
            stale_ids.append(file_id)
    for file_id in stale_ids:
        del index[file_id]

    _save_index(index)

    return ScanResult(
        scanned=scanned,
        added=added,
        updated=updated,
        total=len(index),
        message=f"Indexed {len(index)} mods ({added} new, {updated} refreshed)",
    )


@mod_router.get("", response_model=List[ModEntry])
async def list_mods(search: Optional[str] = None, tag: Optional[str] = None):
    """List all MOD files with metadata.

    Auto-indexes from disk if the index is empty so the library is never blank
    when files are present. Public URLs are always rewritten to the current
    STATIC_BASE_URL and a CORS-safe download_url is included for COEP clients.
    """
    index = _ensure_index_populated()
    entries = [_entry_to_model(data) for data in index.values()]

    if search:
        search_lower = search.lower()
        entries = [
            e for e in entries
            if search_lower in e.title.lower()
            or search_lower in e.author.lower()
            or search_lower in e.filename.lower()
        ]

    if tag:
        entries = [e for e in entries if tag in e.tags]

    # Stable sort by title for predictable library UIs
    entries.sort(key=lambda e: (e.title or e.filename or "").lower())
    return entries


async def _run_scan(pull_remote: bool = True) -> ScanResult:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, lambda: _scan_mods_sync(pull_remote=pull_remote, extract_metadata=True)
    )


@mod_router.get("/scan", response_model=ScanResult)
async def scan_mods_get(remote: bool = False):
    """Scan the local mods directory and refresh the index.

    By default this is local-only (fast, reliable). Pass ``?remote=true`` to also
    attempt a best-effort pull from the configured FTP/SFTP host. Remote pulls
    use short connect timeouts so a down remote never hangs the request for long.
    """
    return await _run_scan(pull_remote=remote)


@mod_router.post("/scan", response_model=ScanResult)
async def scan_mods_post(remote: bool = False):
    """POST alias for scan (mod-player / admin clients often POST sync actions)."""
    return await _run_scan(pull_remote=remote)


@mod_router.post("/reindex", response_model=ScanResult)
async def reindex_mods():
    """Re-extract metadata for all existing indexed mods using openmpt123."""
    def _reindex() -> ScanResult:
        mods_dir = _mods_dir()
        index = _load_index()
        scanned = 0
        updated = 0
        now = datetime.now(timezone.utc).isoformat()

        for file_id, entry in list(index.items()):
            filepath = mods_dir / entry.get("filename", "")
            if not filepath.exists() or not filepath.is_file():
                continue
            if filepath.suffix.lower() not in MOD_EXTENSIONS:
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
            entry["url"] = _public_url(entry.get("filename", ""))
            if changed:
                entry["updated_at"] = now
                updated += 1

        _save_index(index)
        return ScanResult(
            scanned=scanned,
            added=0,
            updated=updated,
            total=len(index),
            message=f"Reindexed {updated}/{scanned} mods",
        )

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _reindex)


@mod_router.get("/{mod_id}/download")
async def download_mod(mod_id: str):
    """CORS-safe binary download proxy for MOD files."""
    index = _ensure_index_populated()
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
        filename=entry["filename"],
        headers={
            "Cross-Origin-Resource-Policy": "cross-origin",
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "public, max-age=3600",
            "Accept-Ranges": "bytes",
        },
    )


@mod_router.get("/{mod_id}", response_model=ModEntry)
async def get_mod(mod_id: str):
    """Get metadata for a specific MOD file."""
    index = _ensure_index_populated()
    if mod_id not in index:
        raise HTTPException(status_code=404, detail="MOD not found")
    return _entry_to_model(index[mod_id])


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
    if entry.get("filename"):
        entry["url"] = _public_url(entry["filename"])

    _save_index(index)
    return _entry_to_model(entry)


def mods_as_song_metadata() -> list[dict]:
    """Expose tracker modules in SongMetadata-shaped dicts for /api/songs?type=mod."""
    index = _ensure_index_populated()
    songs: list[dict] = []
    for raw in index.values():
        entry = _enrich_entry(dict(raw))
        songs.append({
            "id": entry["id"],
            "name": entry.get("title") or entry.get("filename") or entry["id"],
            "title": entry.get("title") or entry.get("filename"),
            "author": entry.get("author") or "",
            "genre": "tracker",
            "rating": None,
            "description": entry.get("notes") or "",
            "tags": entry.get("tags") or ["mod"],
            "duration": entry.get("duration") or None,
            "play_count": 0,
            "last_played": None,
            "created_at": entry.get("added_at") or None,
            "url": entry.get("download_url") or entry.get("url"),
            "size": entry.get("size"),
            "filename": entry.get("filename"),
            "type": "mod",
            "download_url": entry.get("download_url"),
            "downloadUrl": entry.get("downloadUrl"),
            "artist": entry.get("author") or "",
            "fileName": entry.get("filename") or "",
            "durationSeconds": entry.get("durationSeconds"),
        })
    return songs
