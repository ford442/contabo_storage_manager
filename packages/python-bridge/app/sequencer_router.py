"""Sequencer API endpoints for web_sequencer music app.

Provides REST API compatible with the HuggingFace storage manager:
- Songs, patterns, banks, samples CRUD operations
- Index-based listing with metadata
- Static file serving for audio samples
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form
from pydantic import BaseModel, Field

from .config import settings
from .ftp_client import ftp_client

logger = logging.getLogger(__name__)
sequencer_router = APIRouter(prefix="/api", tags=["sequencer"])


# =============================================================================
# Models
# =============================================================================

class CloudItemType:
    SONG = "song"
    PATTERN = "pattern"
    BANK = "bank"
    SAMPLE = "sample"
    AI_GENERATED = "ai-generated"


class SongMetadata(BaseModel):
    id: str
    name: str
    author: str
    date: str
    type: str = "song"
    description: Optional[str] = ""
    filename: str
    size: Optional[int] = None
    url: Optional[str] = None
    version: int = 1
    tags: List[str] = []
    folder: Optional[str] = None
    bpm: Optional[int] = None
    duration: Optional[int] = None


class PatternMetadata(BaseModel):
    id: str
    name: str
    author: str
    date: str
    type: str = "pattern"
    description: Optional[str] = ""
    filename: str
    size: Optional[int] = None
    url: Optional[str] = None
    version: int = 1
    tags: List[str] = []
    folder: Optional[str] = None
    track_count: Optional[int] = None


class BankMetadata(BaseModel):
    id: str
    name: str
    author: str
    date: str
    type: str = "bank"
    description: Optional[str] = ""
    filename: str
    size: Optional[int] = None
    url: Optional[str] = None
    version: int = 1
    tags: List[str] = []
    folder: Optional[str] = None
    preset_count: Optional[int] = None


class SampleMetadata(BaseModel):
    id: str
    name: str
    author: str
    date: str
    type: str = "sample"
    description: Optional[str] = ""
    filename: str
    size: int
    url: Optional[str] = None
    tags: List[str] = []
    folder: Optional[str] = None
    duration_ms: Optional[int] = None


class SongPayload(BaseModel):
    name: str
    author: str
    description: str = ""
    type: str = "song"
    data: Dict[str, Any]
    folder: Optional[str] = None
    tags: List[str] = []


class PatternPayload(BaseModel):
    name: str
    author: str
    description: str = ""
    type: str = "pattern"
    data: Dict[str, Any]
    folder: Optional[str] = None
    tags: List[str] = []


class UploadResponse(BaseModel):
    id: str
    url: str
    timestamp: str
    size: int
    folder: str = "default"


class ListResponse(BaseModel):
    items: List[Dict[str, Any]]
    total: int


# =============================================================================
# Helpers
# =============================================================================

def _get_sequencer_dir() -> Path:
    """Get the sequencer storage directory."""
    base = Path(settings.files_dir)
    seq_dir = base / "sequencer"
    seq_dir.mkdir(parents=True, exist_ok=True)
    return seq_dir


def _get_folder_dir(item_type: str) -> Path:
    """Get folder for a specific item type."""
    seq_dir = _get_sequencer_dir()
    folder_map = {
        "song": "songs",
        "pattern": "patterns",
        "bank": "banks",
        "sample": "samples",
        "ai-generated": "ai-generated",
    }
    folder_name = folder_map.get(item_type, "misc")
    folder_dir = seq_dir / folder_name
    folder_dir.mkdir(parents=True, exist_ok=True)
    return folder_dir


def _get_index_file(item_type: str) -> Path:
    """Get the index file for a specific item type."""
    folder_dir = _get_folder_dir(item_type)
    return folder_dir / f"_{item_type}s.json"


def _load_index(item_type: str) -> List[Dict[str, Any]]:
    """Load the index file for an item type."""
    index_file = _get_index_file(item_type)
    if not index_file.exists():
        return []
    try:
        with open(index_file, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Failed to load index for {item_type}: {e}")
        return []


def _save_index(item_type: str, items: List[Dict[str, Any]]) -> None:
    """Save the index file for an item type."""
    index_file = _get_index_file(item_type)
    try:
        with open(index_file, "w") as f:
            json.dump(items, f, indent=2)
    except IOError as e:
        logger.error(f"Failed to save index for {item_type}: {e}")
        raise


def _generate_id() -> str:
    """Generate a unique ID."""
    return str(uuid.uuid4())


def _get_timestamp() -> str:
    """Get current ISO timestamp."""
    return datetime.now(timezone.utc).isoformat()


def _build_item_path(item_type: str, item_id: str) -> Path:
    """Build the path to an item file."""
    folder_dir = _get_folder_dir(item_type)
    return folder_dir / f"{item_id}.json"


def _build_public_url(item_type: str, item_id: str) -> str:
    """Build the public URL for an item."""
    base_url = settings.static_base_url.rstrip("/")
    folder_map = {
        "song": "songs",
        "pattern": "patterns",
        "bank": "banks",
        "sample": "samples",
        "ai-generated": "ai-generated",
    }
    folder = folder_map.get(item_type, "misc")
    return f"{base_url}/sequencer/{folder}/{item_id}.json"


def _add_to_index(item_type: str, metadata: Dict[str, Any]) -> None:
    """Add an item to the index."""
    items = _load_index(item_type)
    # Remove existing entry with same ID
    items = [i for i in items if i.get("id") != metadata["id"]]
    # Add new entry at the beginning
    items.insert(0, metadata)
    _save_index(item_type, items)


def _remove_from_index(item_type: str, item_id: str) -> None:
    """Remove an item from the index."""
    items = _load_index(item_type)
    items = [i for i in items if i.get("id") != item_id]
    _save_index(item_type, items)


def _get_item_from_index(item_type: str, item_id: str) -> Optional[Dict[str, Any]]:
    """Get an item from the index by ID."""
    items = _load_index(item_type)
    for item in items:
        if item.get("id") == item_id:
            return item
    return None


# =============================================================================
# Song Endpoints
# =============================================================================

@sequencer_router.get("/songs", response_model=List[SongMetadata])
async def list_songs(
    type_filter: Optional[str] = Query(None, alias="type"),
    folder: Optional[str] = None,
    author: Optional[str] = None,
    search: Optional[str] = None,
):
    """List all songs with optional filtering.
    
    Compatible with HuggingFace storage manager API.
    """
    items = _load_index("song")
    
    # Apply filters
    if folder:
        items = [i for i in items if i.get("folder") == folder]
    if author:
        items = [i for i in items if i.get("author") == author]
    if search:
        search_lower = search.lower()
        items = [
            i for i in items 
            if search_lower in i.get("name", "").lower() 
            or search_lower in i.get("description", "").lower()
            or any(search_lower in tag.lower() for tag in i.get("tags", []))
        ]
    
    return [SongMetadata(**item) for item in items]


@sequencer_router.get("/songs/{item_id}")
async def get_song(item_id: str, type: Optional[str] = None):
    """Get a specific song's data by ID.
    
    Returns the full song JSON data, not just metadata.
    """
    # Try to find in index first
    meta = _get_item_from_index("song", item_id)
    if not meta:
        # Try pattern, bank, ai-generated as fallback
        for item_type in ["pattern", "bank", "ai-generated"]:
            meta = _get_item_from_index(item_type, item_id)
            if meta:
                break
    
    if not meta:
        raise HTTPException(status_code=404, detail="Item not found")
    
    # Load the full data
    item_path = _build_item_path(meta.get("type", "song"), item_id)
    if not item_path.exists():
        raise HTTPException(status_code=404, detail="Item file not found")
    
    try:
        with open(item_path, "r") as f:
            data = json.load(f)
        return data
    except (json.JSONDecodeError, IOError) as e:
        raise HTTPException(status_code=500, detail=f"Failed to read item: {e}")


@sequencer_router.post("/songs", response_model=UploadResponse)
async def upload_song(payload: SongPayload):
    """Upload a new song to cloud storage.
    
    Compatible with HuggingFace storage manager API.
    """
    item_id = _generate_id()
    timestamp = _get_timestamp()
    item_type = payload.type if payload.type in ["song", "pattern", "bank", "ai-generated"] else "song"
    
    # Build metadata
    meta = {
        "id": item_id,
        "name": payload.name,
        "author": payload.author,
        "date": timestamp,
        "type": item_type,
        "description": payload.description,
        "filename": f"{item_id}.json",
        "folder": payload.folder or "default",
        "tags": payload.tags,
        "version": 1,
    }
    
    # Add metadata to the payload data
    full_data = {
        **payload.data,
        "_cloud_meta": meta
    }
    
    # Save to file
    item_path = _build_item_path(item_type, item_id)
    try:
        with open(item_path, "w") as f:
            json.dump(full_data, f, indent=2)
        
        # Get file size
        size = item_path.stat().st_size
        meta["size"] = size
        
        # Add to index
        _add_to_index(item_type, meta)
        
        # Upload to external FTP if configured
        try:
            rel_path = f"sequencer/{_get_folder_dir(item_type).name}/{item_id}.json"
            await ftp_client.upload(item_path, rel_path)
        except Exception as e:
            logger.warning(f"FTP upload failed (non-critical): {e}")
        
        return UploadResponse(
            id=item_id,
            url=_build_public_url(item_type, item_id),
            timestamp=timestamp,
            size=size,
            folder=meta["folder"]
        )
    except IOError as e:
        logger.error(f"Failed to save song: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save song: {e}")


@sequencer_router.delete("/songs/{item_id}")
async def delete_song(item_id: str):
    """Delete a song from cloud storage."""
    # Find item in indexes
    meta = None
    item_type = None
    for t in ["song", "pattern", "bank", "ai-generated"]:
        meta = _get_item_from_index(t, item_id)
        if meta:
            item_type = t
            break
    
    if not meta:
        raise HTTPException(status_code=404, detail="Item not found")
    
    # Delete file
    item_path = _build_item_path(item_type, item_id)
    if item_path.exists():
        item_path.unlink()
    
    # Remove from index
    _remove_from_index(item_type, item_id)
    
    return {"success": True, "message": f"Item {item_id} deleted"}


@sequencer_router.patch("/songs/{item_id}")
async def update_song(item_id: str, payload: Dict[str, Any]):
    """Update an existing song (for versioning)."""
    # Find item
    meta = None
    item_type = None
    for t in ["song", "pattern", "bank", "ai-generated"]:
        meta = _get_item_from_index(t, item_id)
        if meta:
            item_type = t
            break
    
    if not meta:
        raise HTTPException(status_code=404, detail="Item not found")
    
    item_path = _build_item_path(item_type, item_id)
    if not item_path.exists():
        raise HTTPException(status_code=404, detail="Item file not found")
    
    # Load existing data
    try:
        with open(item_path, "r") as f:
            existing_data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        raise HTTPException(status_code=500, detail=f"Failed to read item: {e}")
    
    # Update fields
    if "data" in payload:
        existing_data.update(payload["data"])
    
    # Update metadata
    existing_meta = existing_data.get("_cloud_meta", meta)
    if "name" in payload:
        existing_meta["name"] = payload["name"]
    if "description" in payload:
        existing_meta["description"] = payload["description"]
    if "folder" in payload:
        existing_meta["folder"] = payload["folder"]
    if "tags" in payload:
        existing_meta["tags"] = payload["tags"]
    
    existing_meta["version"] = existing_meta.get("version", 1) + 1
    existing_meta["date"] = _get_timestamp()
    existing_data["_cloud_meta"] = existing_meta
    
    # Save updated file
    try:
        with open(item_path, "w") as f:
            json.dump(existing_data, f, indent=2)
        
        # Update index
        existing_meta["size"] = item_path.stat().st_size
        _add_to_index(item_type, existing_meta)
        
        return UploadResponse(
            id=item_id,
            url=_build_public_url(item_type, item_id),
            timestamp=existing_meta["date"],
            size=existing_meta["size"],
            folder=existing_meta.get("folder", "default")
        )
    except IOError as e:
        raise HTTPException(status_code=500, detail=f"Failed to update item: {e}")


# =============================================================================
# Pattern Endpoints
# =============================================================================

@sequencer_router.get("/patterns", response_model=List[PatternMetadata])
async def list_patterns(
    folder: Optional[str] = None,
    author: Optional[str] = None,
):
    """List all patterns."""
    items = _load_index("pattern")
    
    if folder:
        items = [i for i in items if i.get("folder") == folder]
    if author:
        items = [i for i in items if i.get("author") == author]
    
    return [PatternMetadata(**item) for item in items]


@sequencer_router.post("/patterns", response_model=UploadResponse)
async def upload_pattern(payload: PatternPayload):
    """Upload a new pattern."""
    item_id = _generate_id()
    timestamp = _get_timestamp()
    
    meta = {
        "id": item_id,
        "name": payload.name,
        "author": payload.author,
        "date": timestamp,
        "type": "pattern",
        "description": payload.description,
        "filename": f"{item_id}.json",
        "folder": payload.folder or "default",
        "tags": payload.tags,
        "version": 1,
    }
    
    full_data = {
        **payload.data,
        "_cloud_meta": meta
    }
    
    item_path = _build_item_path("pattern", item_id)
    try:
        with open(item_path, "w") as f:
            json.dump(full_data, f, indent=2)
        
        size = item_path.stat().st_size
        meta["size"] = size
        _add_to_index("pattern", meta)
        
        return UploadResponse(
            id=item_id,
            url=_build_public_url("pattern", item_id),
            timestamp=timestamp,
            size=size,
            folder=meta["folder"]
        )
    except IOError as e:
        raise HTTPException(status_code=500, detail=f"Failed to save pattern: {e}")


# =============================================================================
# Bank Endpoints
# =============================================================================

@sequencer_router.get("/banks", response_model=List[BankMetadata])
async def list_banks(
    folder: Optional[str] = None,
    author: Optional[str] = None,
):
    """List all banks."""
    items = _load_index("bank")
    
    if folder:
        items = [i for i in items if i.get("folder") == folder]
    if author:
        items = [i for i in items if i.get("author") == author]
    
    return [BankMetadata(**item) for item in items]


# =============================================================================
# Sample Endpoints
# =============================================================================

@sequencer_router.get("/samples", response_model=List[SampleMetadata])
async def list_samples():
    """List all samples."""
    items = _load_index("sample")
    return [SampleMetadata(**item) for item in items]


@sequencer_router.post("/samples")
async def upload_sample(
    file: UploadFile = File(...),
    author: str = Form(...),
    description: str = Form(""),
):
    """Upload a new audio sample."""
    sample_id = _generate_id()
    timestamp = _get_timestamp()
    
    # Determine file extension
    ext = Path(file.filename or "").suffix or ".wav"
    filename = f"{sample_id}{ext}"
    
    # Save file
    sample_dir = _get_folder_dir("sample")
    sample_path = sample_dir / filename
    
    try:
        content = await file.read()
        with open(sample_path, "wb") as f:
            f.write(content)
        
        size = len(content)
        
        meta = {
            "id": sample_id,
            "name": file.filename or filename,
            "author": author,
            "date": timestamp,
            "type": "sample",
            "description": description,
            "filename": filename,
            "size": size,
            "folder": "default",
            "tags": [],
        }
        
        _add_to_index("sample", meta)
        
        # Upload to external FTP if configured
        try:
            rel_path = f"sequencer/samples/{filename}"
            await ftp_client.upload(sample_path, rel_path)
        except Exception as e:
            logger.warning(f"FTP upload failed (non-critical): {e}")
        
        return UploadResponse(
            id=sample_id,
            url=_build_public_url("sample", sample_id).replace(".json", ext),
            timestamp=timestamp,
            size=size,
            folder="default"
        )
    except IOError as e:
        raise HTTPException(status_code=500, detail=f"Failed to save sample: {e}")


@sequencer_router.get("/samples/{sample_id}")
async def get_sample(sample_id: str):
    """Get sample metadata."""
    meta = _get_item_from_index("sample", sample_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Sample not found")
    return SampleMetadata(**meta)


# =============================================================================
# Combined List Endpoint (for HuggingFace compatibility)
# =============================================================================

@sequencer_router.get("/items", response_model=List[Dict[str, Any]])
async def list_all_items(
    type: Optional[str] = Query(None),
    folder: Optional[str] = None,
):
    """List all items of a specific type or all types.
    
    This is a compatibility endpoint that matches the HuggingFace API structure.
    """
    results = []
    
    types = [type] if type else ["song", "pattern", "bank", "ai-generated"]
    
    for t in types:
        items = _load_index(t)
        if folder:
            items = [i for i in items if i.get("folder") == folder]
        results.extend(items)
    
    # Sort by date, newest first
    results.sort(key=lambda x: x.get("date", ""), reverse=True)
    
    return results


# =============================================================================
# Health Check
# =============================================================================

@sequencer_router.get("/sequencer/health")
async def sequencer_health():
    """Health check for sequencer API."""
    return {
        "status": "healthy",
        "service": "sequencer-storage",
        "timestamp": _get_timestamp(),
        "version": "1.0.0"
    }
