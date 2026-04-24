from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import json
import os
from pathlib import Path

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
                return json.load(f)
        except:
            return {}
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
    """Scan FILES_DIR/mods/ and update the index."""
    mods_dir = _mods_dir()
    index = _load_index()
    
    scanned = 0
    added = 0
    updated = 0
    
    from datetime import datetime
    now = datetime.utcnow().isoformat()
    
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
        
        if file_id not in index:
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
                "updated_at": now
            }
            added += 1
        else:
            # Update size and timestamp, preserve user metadata
            entry = index[file_id]
            entry["size"] = size
            entry["updated_at"] = now
            updated += 1
    
    _save_index(index)
    
    return ScanResult(
        scanned=scanned,
        added=added,
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
    
    from datetime import datetime
    entry["updated_at"] = datetime.utcnow().isoformat()
    
    _save_index(index)
    
    return ModEntry(**entry)
