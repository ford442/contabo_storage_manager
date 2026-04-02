"""API endpoints for shaders, images, and ratings."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Union

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .config import settings

logger = logging.getLogger(__name__)
api_router = APIRouter(prefix="/api", tags=["api"])


# ====================== Models ======================

class ShaderParam(BaseModel):
    """Shader parameter definition."""
    name: str
    label: Optional[str] = None
    default: float = 0.5
    min: float = 0.0
    max: float = 1.0
    step: Optional[float] = 0.01
    description: Optional[str] = ""

class ShaderMetadata(BaseModel):
    id: str
    name: str
    author: str = ""
    date: str = ""
    type: str = "shader"
    description: str = ""
    filename: str = ""
    tags: List[str] = []
    rating: Optional[int] = Field(None, ge=0, le=5)  # 0-5 stars, 0 = errors
    source: str = "upload"
    original_id: Optional[str] = None
    format: str = "wgsl"
    converted: bool = False
    has_errors: bool = False
    params: Optional[List[ShaderParam]] = None  # Shader parameters


class ShaderRatingUpdate(BaseModel):
    rating: int = Field(..., ge=0, le=5, description="Rating 0-5 stars. 0 = has errors")
    notes: Optional[str] = None


class MetaPatch(BaseModel):
    """Partial update for shader metadata."""
    name: Optional[str] = None
    author: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    params: Optional[List[ShaderParam]] = None  # NEW: Support for shader params


class ImageRecord(BaseModel):
    url: str
    description: Optional[str] = None
    tags: List[str] = []


class SongRecord(BaseModel):
    id: str
    title: str
    artist: str = ""
    url: str = ""
    duration: int = 0
    tags: List[str] = []


class ShaderListResponse(BaseModel):
    shaders: List[ShaderMetadata]
    total: int
    page: int = 1
    per_page: int = 100


# ====================== Helpers ======================

def _get_shaders_dir() -> Path:
    """Get the shaders storage directory."""
    base = Path(settings.files_dir)
    shaders_dir = base / "shaders"
    shaders_dir.mkdir(parents=True, exist_ok=True)
    return shaders_dir


def _load_shader_meta(shader_dir: Path) -> Optional[dict]:
    """Load shader metadata from its meta.json file."""
    meta_file = shader_dir / "meta.json"
    if not meta_file.exists():
        return None
    try:
        with open(meta_file, "r") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return None


def _save_shader_meta(shader_dir: Path, meta: dict) -> None:
    """Save shader metadata to its meta.json file."""
    meta_file = shader_dir / "meta.json"
    with open(meta_file, "w") as f:
        json.dump(meta, f, indent=2)


# ====================== Endpoints ======================

@api_router.get("/shaders", response_model=ShaderListResponse)
async def list_shaders(
    page: int = Query(1, ge=1),
    per_page: int = Query(100, ge=1, le=1000),
    tag: Optional[str] = None,
    rating: Optional[int] = None,
    sort_by: str = Query("name", regex="^(name|date|rating)$")
):
    """List all shaders with pagination, filtering, and sorting."""
    shaders_dir = _get_shaders_dir()
    shaders = []
    
    for shader_dir in sorted(shaders_dir.iterdir()):
        if not shader_dir.is_dir():
            continue
        meta = _load_shader_meta(shader_dir)
        if meta:
            # Filter by tag if specified
            if tag and tag not in meta.get("tags", []):
                continue
            # Filter by rating if specified
            if rating is not None and meta.get("rating") != rating:
                continue
            shaders.append(meta)
    
    # Sort shaders
    reverse = sort_by in ("rating", "date")
    if sort_by == "rating":
        shaders.sort(key=lambda s: s.get("rating", 0) or 0, reverse=reverse)
    elif sort_by == "date":
        shaders.sort(key=lambda s: s.get("date", ""), reverse=reverse)
    elif sort_by == "name":
        shaders.sort(key=lambda s: s.get("name", "").lower())
    
    total = len(shaders)
    start = (page - 1) * per_page
    end = start + per_page
    
    return ShaderListResponse(
        shaders=shaders[start:end],
        total=total,
        page=page,
        per_page=per_page
    )


@api_router.get("/shaders/{shader_id}", response_model=ShaderMetadata)
async def get_shader(shader_id: str):
    """Get a single shader's metadata by ID."""
    shaders_dir = _get_shaders_dir()
    shader_dir = shaders_dir / shader_id
    
    if not shader_dir.exists():
        raise HTTPException(status_code=404, detail="Shader not found")
    
    meta = _load_shader_meta(shader_dir)
    if not meta:
        raise HTTPException(status_code=404, detail="Shader metadata not found")
    
    return ShaderMetadata(**meta)


@api_router.post("/shaders", response_model=ShaderMetadata)
async def create_shader(shader_data: dict):
    """Create a new shader entry."""
    shader_id = shader_data.get("id")
    if not shader_id:
        raise HTTPException(status_code=400, detail="Shader ID is required")
    
    shaders_dir = _get_shaders_dir()
    shader_dir = shaders_dir / shader_id
    shader_dir.mkdir(parents=True, exist_ok=True)
    
    # Set defaults
    meta = {
        "id": shader_id,
        "name": shader_data.get("name", shader_id),
        "author": shader_data.get("author", ""),
        "date": datetime.now(timezone.utc).isoformat(),
        "type": "shader",
        "description": shader_data.get("description", ""),
        "filename": f"{shader_id}.wgsl",
        "tags": shader_data.get("tags", []),
        "rating": shader_data.get("rating"),
        "source": shader_data.get("source", "upload"),
        "original_id": shader_data.get("original_id"),
        "format": shader_data.get("format", "wgsl"),
        "converted": shader_data.get("converted", False),
        "has_errors": shader_data.get("has_errors", False),
        "params": shader_data.get("params"),  # Save params if provided
    }
    
    _save_shader_meta(shader_dir, meta)
    
    # Write shader code if provided
    code = shader_data.get("code")
    if code:
        shader_file = shader_dir / f"{shader_id}.wgsl"
        with open(shader_file, "w") as f:
            f.write(code)
    
    return ShaderMetadata(**meta)


@api_router.put("/shaders/{shader_id}", response_model=ShaderMetadata)
async def update_shader(shader_id: str, payload: MetaPatch):
    """Update shader metadata (including params)."""
    shaders_dir = _get_shaders_dir()
    shader_dir = shaders_dir / shader_id
    
    if not shader_dir.exists():
        raise HTTPException(status_code=404, detail="Shader not found")
    
    meta = _load_shader_meta(shader_dir)
    if not meta:
        raise HTTPException(status_code=404, detail="Shader metadata not found")
    
    # Update fields if provided
    updated = {}
    if payload.name is not None:
        meta["name"] = payload.name
        updated["name"] = payload.name
    if payload.author is not None:
        meta["author"] = payload.author
        updated["author"] = payload.author
    if payload.description is not None:
        meta["description"] = payload.description
        updated["description"] = payload.description
    if payload.tags is not None:
        meta["tags"] = payload.tags
        updated["tags"] = f"{len(payload.tags)} tags"
    # NEW: Handle params update
    if payload.params is not None:
        meta["params"] = [p.model_dump() for p in payload.params]
        updated["params"] = f"{len(payload.params)} parameters"
    
    if updated:
        meta["date"] = datetime.now(timezone.utc).isoformat()
        _save_shader_meta(shader_dir, meta)
        logger.info(f"Updated shader {shader_id}: {updated}")
    
    return ShaderMetadata(**meta)


@api_router.post("/shaders/{shader_id}/rate")
async def rate_shader(shader_id: str, rating_update: ShaderRatingUpdate):
    """Rate a shader or mark it as having errors (rating=0)."""
    shaders_dir = _get_shaders_dir()
    shader_dir = shaders_dir / shader_id
    
    if not shader_dir.exists():
        raise HTTPException(status_code=404, detail="Shader not found")
    
    meta = _load_shader_meta(shader_dir)
    if not meta:
        raise HTTPException(status_code=404, detail="Shader metadata not found")
    
    meta["rating"] = rating_update.rating
    meta["has_errors"] = rating_update.rating == 0
    if rating_update.notes:
        meta["rating_notes"] = rating_update.notes
    
    _save_shader_meta(shader_dir, meta)
    
    return {
        "shader_id": shader_id,
        "rating": rating_update.rating,
        "has_errors": meta["has_errors"],
        "message": "Rating updated successfully"
    }


@api_router.get("/shaders/{shader_id}/rating")
async def get_shader_rating(shader_id: str):
    """Get a shader's current rating."""
    shaders_dir = _get_shaders_dir()
    shader_dir = shaders_dir / shader_id
    
    if not shader_dir.exists():
        raise HTTPException(status_code=404, detail="Shader not found")
    
    meta = _load_shader_meta(shader_dir)
    if not meta:
        raise HTTPException(status_code=404, detail="Shader metadata not found")
    
    return {
        "shader_id": shader_id,
        "rating": meta.get("rating"),
        "has_errors": meta.get("has_errors", False),
        "notes": meta.get("rating_notes", "")
    }


@api_router.get("/shaders/{shader_id}/code")
async def get_shader_code(shader_id: str):
    """Get a shader's WGSL source code."""
    shaders_dir = _get_shaders_dir()
    shader_dir = shaders_dir / shader_id
    
    if not shader_dir.exists():
        raise HTTPException(status_code=404, detail="Shader not found")
    
    # Try to read the .wgsl file
    wgsl_file = shader_dir / f"{shader_id}.wgsl"
    if not wgsl_file.exists():
        # Try alternative names
        for f in shader_dir.glob("*.wgsl"):
            wgsl_file = f
            break
    
    if not wgsl_file.exists():
        raise HTTPException(status_code=404, detail="Shader code not found")
    
    try:
        with open(wgsl_file, "r") as f:
            code = f.read()
        return {"id": shader_id, "code": code}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read shader: {str(e)}")


@api_router.get("/shaders-errors")
async def list_shaders_with_errors():
    """List all shaders that have been marked with errors (rating=0)."""
    shaders_dir = _get_shaders_dir()
    error_shaders = []
    
    for shader_dir in shaders_dir.iterdir():
        if not shader_dir.is_dir():
            continue
        meta = _load_shader_meta(shader_dir)
        if meta and meta.get("has_errors"):
            error_shaders.append({
                "id": meta.get("id"),
                "name": meta.get("name"),
                "rating": meta.get("rating"),
                "notes": meta.get("rating_notes", "")
            })
    
    return {"shaders_with_errors": error_shaders, "total": len(error_shaders)}


@api_router.get("/images")
async def list_images():
    """List all recorded images."""
    base = Path(settings.files_dir)
    images_file = base / "images.json"
    
    if not images_file.exists():
        return {"images": []}
    
    with open(images_file, "r") as f:
        return json.load(f)


@api_router.post("/images")
async def record_image(record: ImageRecord):
    """Record a new image."""
    base = Path(settings.files_dir)
    images_file = base / "images.json"
    
    data = {"images": []}
    if images_file.exists():
        with open(images_file, "r") as f:
            data = json.load(f)
    
    image_entry = {
        "url": record.url,
        "description": record.description,
        "tags": record.tags,
        "recorded_at": datetime.now(timezone.utc).isoformat()
    }
    data["images"].append(image_entry)
    
    with open(images_file, "w") as f:
        json.dump(data, f, indent=2)
    
    return image_entry


@api_router.get("/songs")
async def list_songs():
    """List all recorded songs."""
    base = Path(settings.files_dir)
    songs_file = base / "songs.json"
    
    if not songs_file.exists():
        return {"songs": []}
    
    with open(songs_file, "r") as f:
        return json.load(f)


@api_router.post("/songs")
async def record_song(record: SongRecord):
    """Record a new song."""
    base = Path(settings.files_dir)
    songs_file = base / "songs.json"
    
    data = {"songs": []}
    if songs_file.exists():
        with open(songs_file, "r") as f:
            data = json.load(f)
    
    song_entry = {
        "id": record.id,
        "title": record.title,
        "artist": record.artist,
        "url": record.url,
        "duration": record.duration,
        "tags": record.tags,
        "recorded_at": datetime.now(timezone.utc).isoformat()
    }
    data["songs"].append(song_entry)
    
    with open(songs_file, "w") as f:
        json.dump(data, f, indent=2)
    
    return song_entry


@api_router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "storage-manager-api",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
