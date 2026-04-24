"""API endpoints for shaders, images, and ratings."""

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional, Union

import os
import uuid
from fastapi import APIRouter, HTTPException, Query, Form, File, UploadFile, Request
from fastapi.responses import FileResponse, StreamingResponse, RedirectResponse
from pydantic import BaseModel, Field

from .config import settings
from .flac_client import register_song_with_flac_player
from . import presets
from pydub import AudioSegment
from pydub.exceptions import CouldntDecodeError

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


class MapConfig(BaseModel):
    id: str
    name: str
    baseColor: str = "#00d9ff"
    accentColor: str = "#ffffff"
    scanlineIntensity: float = 0.25
    pixelGridIntensity: float = 0.8
    subpixelIntensity: float = 0.6
    glowIntensity: float = 1.0
    backgroundPattern: str = "hex"
    animationSpeed: float = 0.5
    musicTrackId: Optional[str] = None
    shaderUrl: Optional[str] = None
    adventureGoals: Optional[List[str]] = None
    # Extra fields from map_config are allowed via model_config
    model_config = {"extra": "allow"}


class MapListResponse(BaseModel):
    maps: List[MapConfig]
    total: int


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


@api_router.get("/maps", response_model=MapListResponse)
async def list_maps():
    """List all LCD table maps derived from shaders tagged 'lcd-map'."""
    shaders_dir = _get_shaders_dir()
    maps = []
    base_url = settings.static_base_url
    
    for shader_dir in sorted(shaders_dir.iterdir()):
        if not shader_dir.is_dir():
            continue
        meta = _load_shader_meta(shader_dir)
        if not meta:
            continue
        tags = meta.get("tags", [])
        if "lcd-map" not in tags:
            continue
        
        # Extract map config from params or top-level fields
        params = meta.get("params", {})
        if isinstance(params, list):
            # Convert list of ShaderParam to dict
            params = {p.get("name"): p.get("default") for p in params if isinstance(p, dict)}
        elif params is None:
            params = {}
        
        shader_id = meta.get("id", shader_dir.name)
        # Merge top-level map_config dict if present
        map_config_raw = meta.get("map_config", {})
        if isinstance(map_config_raw, dict):
            params = {**params, **map_config_raw}

        map_config = MapConfig(
            id=shader_id,
            name=meta.get("name", shader_id),
            baseColor=params.get("baseColor", params.get("base_color", "#00d9ff")),
            accentColor=params.get("accentColor", params.get("accent_color", "#ffffff")),
            scanlineIntensity=float(params.get("scanlineIntensity", params.get("scanline_intensity", 0.25))),
            pixelGridIntensity=float(params.get("pixelGridIntensity", params.get("pixel_grid_intensity", 0.8))),
            subpixelIntensity=float(params.get("subpixelIntensity", params.get("subpixel_intensity", 0.6))),
            glowIntensity=float(params.get("glowIntensity", params.get("glow_intensity", 1.0))),
            backgroundPattern=params.get("backgroundPattern", params.get("background_pattern", "hex")),
            animationSpeed=float(params.get("animationSpeed", params.get("animation_speed", 0.5))),
            musicTrackId=params.get("musicTrackId", params.get("music_track_id")),
            shaderUrl=f"{base_url}/shaders/{shader_id}/code",
            adventureGoals=params.get("adventureGoals", params.get("adventure_goals")),
        )
        maps.append(map_config)
    
    return MapListResponse(maps=maps, total=len(maps))


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


# ====================== FLAC Player Song API ======================

class SongMetadata(BaseModel):
    """Song metadata model matching flac_player expectations."""
    id: str
    name: str
    title: Optional[str] = None
    author: Optional[str] = None
    genre: Optional[str] = None
    rating: Optional[int] = Field(None, ge=1, le=10)
    description: Optional[str] = None
    tags: List[str] = []
    duration: Optional[float] = None
    play_count: int = 0
    last_played: Optional[str] = None
    created_at: Optional[str] = None
    url: Optional[str] = None
    size: Optional[int] = None
    filename: Optional[str] = None
    type: Optional[str] = None


class SongStats(BaseModel):
    total_tracks: int = 0
    rated_4plus: int = 0
    total_duration_hours: int = 0
    total_play_count: int = 0
    untagged_count: int = 0
    trash_count: int = 0
    unique_tags: int = 0
    top_tags: List[dict] = []


class SongPatch(BaseModel):
    """Partial update for song metadata."""
    name: Optional[str] = None
    title: Optional[str] = None
    author: Optional[str] = None
    genre: Optional[str] = None
    rating: Optional[int] = Field(None, ge=1, le=10)
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    last_played: Optional[str] = None
    play_count: Optional[int] = None


def _get_songs_file() -> Path:
    """Get the songs JSON file path."""
    base = Path(settings.files_dir)
    songs_file = base / "songs.json"
    return songs_file


def _load_songs() -> List[dict]:
    """Load songs from JSON file."""
    songs_file = _get_songs_file()
    if not songs_file.exists():
        return []
    try:
        with open(songs_file, "r") as f:
            data = json.load(f)
            return data.get("songs", []) if isinstance(data, dict) else data
    except (json.JSONDecodeError, IOError):
        return []


def _save_songs(songs: List[dict]):
    """Save songs to JSON file."""
    songs_file = _get_songs_file()
    songs_file.parent.mkdir(parents=True, exist_ok=True)
    with open(songs_file, "w") as f:
        json.dump({"songs": songs}, f, indent=2)


@api_router.get("/songs", response_model=List[SongMetadata])
async def list_songs(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    rating_gte: Optional[int] = Query(None, ge=0, le=10),
    rating_lt: Optional[int] = Query(None, ge=0, le=10),
    tags: Optional[str] = Query(None),
    untagged: bool = Query(False),
    search: Optional[str] = Query(None),
    sort_by: str = Query("date"),
    sort_desc: bool = Query(True),
    exclude_id: Optional[str] = Query(None),
):
    """List all songs with filtering and sorting (flac_player compatible)."""
    songs = _load_songs()
    
    # Apply filters
    if rating_gte is not None:
        songs = [s for s in songs if (s.get("rating") or 0) >= rating_gte]
    if rating_lt is not None:
        songs = [s for s in songs if (s.get("rating") or 0) < rating_lt]
    if tags:
        tag_list = [t.strip() for t in tags.split(",")]
        songs = [s for s in songs if any(t in s.get("tags", []) for t in tag_list)]
    if untagged:
        songs = [s for s in songs if not s.get("tags")]
    if search:
        search_lower = search.lower()
        songs = [s for s in songs if (
            search_lower in s.get("name", "").lower() or
            search_lower in s.get("title", "").lower() or
            search_lower in s.get("author", "").lower() or
            search_lower in s.get("artist", "").lower() or
            search_lower in s.get("description", "").lower() or
            any(search_lower in str(tag).lower() for tag in s.get("tags", []))
        )]
    if exclude_id:
        songs = [s for s in songs if s.get("id") != exclude_id]
    
    # Sort
    reverse = sort_desc
    if sort_by == "rating":
        songs.sort(key=lambda s: s.get("rating", 0) or 0, reverse=reverse)
    elif sort_by == "name":
        songs.sort(key=lambda s: s.get("name", "").lower(), reverse=reverse)
    elif sort_by == "play_count":
        songs.sort(key=lambda s: s.get("play_count", 0), reverse=reverse)
    elif sort_by == "last_played":
        songs.sort(key=lambda s: s.get("last_played", ""), reverse=reverse)
    elif sort_by == "random":
        import random
        random.shuffle(songs)
    else:  # date
        songs.sort(key=lambda s: s.get("created_at", s.get("date", "")), reverse=reverse)
    
    # Ensure absolute URLs
    base_url = str(settings.static_base_url).rstrip("/")
    api_base = "https://storage.noahcohn.com"
    for song in songs:
        url = song.get("url")
        if not url and song.get("filename"):
            song["url"] = f"{base_url}/audio/music/{song['filename']}"
        elif url and url.startswith("/"):
            song["url"] = f"{api_base}{url}"
    
    # Apply pagination
    total = len(songs)
    songs = songs[offset:offset + limit]
    
    return songs


@api_router.get("/songs/stats", response_model=SongStats)
async def get_songs_stats():
    """Get library statistics for flac_player."""
    songs = _load_songs()
    
    if not songs:
        return SongStats()
    
    # Calculate stats
    total_duration = sum(s.get("duration", 0) or 0 for s in songs)
    total_play_count = sum(s.get("play_count", 0) or 0 for s in songs)
    
    # Count unique tags
    all_tags = {}
    untagged = 0
    for song in songs:
        song_tags = song.get("tags", [])
        if not song_tags:
            untagged += 1
        for tag in song_tags:
            all_tags[tag] = all_tags.get(tag, 0) + 1
    
    top_tags = [{"name": k, "count": v} for k, v in sorted(all_tags.items(), key=lambda x: -x[1])[:20]]
    
    return SongStats(
        total_tracks=len(songs),
        rated_4plus=sum(1 for s in songs if (s.get("rating") or 0) >= 4),
        total_duration_hours=total_duration // 3600,
        total_play_count=total_play_count,
        untagged_count=untagged,
        trash_count=0,
        unique_tags=len(all_tags),
        top_tags=top_tags
    )


@api_router.get("/songs/tags")
async def get_songs_tags():
    """Get all tags with counts for flac_player."""
    songs = _load_songs()
    
    tag_counts = {}
    for song in songs:
        for tag in song.get("tags", []):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    
    tags = [{"name": k, "count": v} for k, v in sorted(tag_counts.items(), key=lambda x: -x[1])]
    return {"tags": tags}


@api_router.get("/songs/{song_id}", response_model=SongMetadata)
async def get_song(song_id: str):
    """Get a single song by ID."""
    songs = _load_songs()
    song = next((s for s in songs if s.get("id") == song_id), None)
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    
    # Add URL if missing
    if not song.get("url") and song.get("filename"):
        base_url = str(settings.static_base_url).rstrip("/")
        song["url"] = f"{base_url}/audio/music/{song['filename']}"
    
    return song


@api_router.post("/songs/{song_id}/play")
async def record_song_play(song_id: str):
    """Record that a song was played."""
    songs = _load_songs()
    song = next((s for s in songs if s.get("id") == song_id), None)
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    
    song["play_count"] = (song.get("play_count", 0) or 0) + 1
    song["last_played"] = datetime.now(timezone.utc).isoformat()
    
    _save_songs(songs)
    return {"success": True, "play_count": song["play_count"]}


@api_router.patch("/songs/{song_id}")
async def patch_song(song_id: str, patch: SongPatch):
    """Partially update song metadata."""
    songs = _load_songs()
    song = next((s for s in songs if s.get("id") == song_id), None)
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    
    # Apply updates
    updates = patch.model_dump(exclude_unset=True)
    for field, value in updates.items():
        if value is not None:
            song[field] = value
    
    _save_songs(songs)
    return {"success": True, "song": song}


@api_router.post("/songs/{song_id}/trash")
async def trash_song(song_id: str):
    """Mark a song as trashed."""
    songs = _load_songs()
    song = next((s for s in songs if s.get("id") == song_id), None)
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    
    song["trashed"] = True
    song["trashed_at"] = datetime.now(timezone.utc).isoformat()
    
    _save_songs(songs)
    return {"success": True}


@api_router.delete("/songs/{song_id}")
async def delete_song(song_id: str):
    """Permanently delete a song and its audio file."""
    songs = _load_songs()
    song = next((s for s in songs if s.get("id") == song_id), None)
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    
    # Remove from index
    songs = [s for s in songs if s.get("id") != song_id]
    _save_songs(songs)
    
    # Try to delete the audio file if it exists locally
    filename = song.get("filename")
    if filename:
        base = Path(settings.files_dir)
        audio_path = base / "audio" / "music" / filename
        try:
            if audio_path.exists():
                audio_path.unlink()
        except OSError as exc:
            logger.warning("Failed to delete audio file %s: %s", audio_path, exc)
    
    return {"success": True, "deleted": song_id}


@api_router.get("/music/{song_id}")
async def stream_music_file(song_id: str):
    """Stream a music file by song ID."""
    songs = _load_songs()
    song = next((s for s in songs if s.get("id") == song_id), None)
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    
    # Get the audio directory
    base = Path(settings.files_dir)
    audio_dir = base / "audio" / "music"
    
    def _media_type_for_ext(path: Path) -> str:
        ext = path.suffix.lower()
        if ext == ".flac":
            return "audio/flac"
        elif ext == ".wav":
            return "audio/wav"
        elif ext == ".ogg":
            return "audio/ogg"
        elif ext == ".mp3":
            return "audio/mpeg"
        elif ext == ".m4a":
            return "audio/mp4"
        elif ext == ".aac":
            return "audio/aac"
        return "audio/mpeg"

    # Try to find the file by filename or song_id
    filename = song.get("filename")
    if filename:
        file_path = audio_dir / filename
        if file_path.exists():
            return FileResponse(
                file_path,
                media_type=_media_type_for_ext(file_path),
                headers={"Accept-Ranges": "bytes"}
            )

    # Try common extensions
    for ext in [".mp3", ".flac", ".wav", ".ogg", ".m4a", ".aac"]:
        file_path = audio_dir / f"{song_id}{ext}"
        if file_path.exists():
            return FileResponse(
                file_path,
                media_type=_media_type_for_ext(file_path),
                headers={"Accept-Ranges": "bytes"}
            )
    
    raise HTTPException(status_code=404, detail="Audio file not found")


@api_router.get("/songs/{song_id}/suggest-tags")
async def suggest_song_tags(song_id: str):
    """Suggest tags for a song based on its metadata (simple implementation)."""
    songs = _load_songs()
    song = next((s for s in songs if s.get("id") == song_id), None)
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    
    # Simple suggestion logic based on genre and existing tags
    suggestions = []
    
    if song.get("genre"):
        suggestions.append(song["genre"].lower())
    
    # Common music tags based on name/title
    name = (song.get("name") or song.get("title") or "").lower()
    if any(word in name for word in ["electronic", "synth", "techno", "edm"]):
        suggestions.extend(["electronic", "synth"])
    if any(word in name for word in ["rock", "guitar", "band"]):
        suggestions.extend(["rock", "guitar"])
    if any(word in name for word in ["ambient", "chill", "relax", "sleep"]):
        suggestions.extend(["ambient", "chill"])
    if any(word in name for word in ["upbeat", "fast", "dance", "party"]):
        suggestions.extend(["upbeat", "dance"])
    
    # Remove duplicates and already existing tags
    existing = set(t.lower() for t in song.get("tags", []))
    suggestions = [s for s in suggestions if s not in existing]
    
    return {"suggestions": list(set(suggestions)), "source": "auto"}


@api_router.post("/songs/upload")
async def upload_song(
    file: UploadFile = File(...),
    title: str = Form(...),
    author: str = Form("Unknown"),
    genre: str = Form(""),
    description: str = Form(""),
    tags: str = Form(""),
):
    """Upload a music file (MP3, FLAC, WAV, OGG) and add it to the songs library.

    This endpoint always converts uploaded audio to high-quality FLAC using
    pydub/ffmpeg and stores the result under audio/music/. It then indexes
    the song in songs.json and notifies the external FLAC player backend if configured.
    """
    # Validate file extension
    filename = file.filename or ""
    ext = Path(filename).suffix.lower()
    allowed_exts = ['.mp3', '.flac', '.wav', '.ogg', '.m4a', '.aac']

    if ext not in allowed_exts:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type. Allowed: {', '.join(allowed_exts)}"
        )

    # Generate unique ID and target filename (always .flac)
    song_id = str(uuid.uuid4())[:8]
    safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).strip()
    safe_title = safe_title.replace(' ', '_') or "untitled"
    storage_filename = f"{song_id}_{safe_title}.flac"

    # Prepare directories
    base = Path(settings.files_dir)
    music_dir = base / "audio" / "music"
    music_dir.mkdir(parents=True, exist_ok=True)

    # Read uploaded content to temp file first
    temp_path = Path("/tmp") / f"{uuid.uuid4()}_{filename}"
    content = await file.read()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail=f"File exceeds {settings.max_upload_mb} MB limit")
    temp_path.write_bytes(content)

    try:
        try:
            audio = AudioSegment.from_file(str(temp_path))
        except (CouldntDecodeError, FileNotFoundError):
            raise HTTPException(status_code=400, detail="Could not decode file. Is ffmpeg installed on the server?")

        # Export to high-quality FLAC
        dest = music_dir / storage_filename
        audio.export(dest, format="flac", parameters=["-compression_level", "8"])
        duration_sec = len(audio) / 1000.0
        size_bytes = dest.stat().st_size if dest.exists() else len(content)
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except Exception:
            pass

    # Parse tags
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]

    # Create song entry
    song = {
        "id": song_id,
        "name": f"{title}.flac",
        "title": title,
        "author": author,
        "genre": genre or None,
        "rating": None,
        "description": description or f"Uploaded on {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
        "tags": tag_list,
        "duration": round(duration_sec, 2),
        "play_count": 0,
        "last_played": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "filename": storage_filename,
        "url": f"https://storage.noahcohn.com/api/music/{song_id}",
        "size": size_bytes,
    }

    # Add to songs index
    songs = _load_songs()
    songs.append(song)
    _save_songs(songs)

    # Notify external FLAC Player backend if configured
    base_url = str(settings.static_base_url).rstrip("/")
    public_url = f"{base_url}/audio/music/{storage_filename}"
    rounded_duration_sec = round(duration_sec, 2)
    await register_song_with_flac_player(
    filename=song["name"],
    public_url=public_url,
    title=title,
    author=author,
    tags=tag_list,
    genre=genre or None,
    duration=round(duration_sec, 2) if duration_sec is not None else None,
    filename_on_storage=storage_filename,
    auto_enrich=True,
)


    return {
        "success": True,
        "song": song,
        "message": f"Uploaded {filename} successfully"
    }


# ====================== Share API ======================

class ShareCreateRequest(BaseModel):
    track_ids: List[str]
    title: str
    expires_in_days: Optional[int] = 30


class ShareCreateResponse(BaseModel):
    share_id: str
    title: str
    track_count: int
    full_url: str


class ShareGetResponse(BaseModel):
    title: str
    tracks: List[SongMetadata]


def _get_shares_file() -> Path:
    """Get the shares JSON file path."""
    base = Path(settings.files_dir)
    shares_file = base / "shares.json"
    return shares_file


def _load_shares() -> dict:
    """Load shares from JSON file."""
    shares_file = _get_shares_file()
    if not shares_file.exists():
        return {}
    try:
        with open(shares_file, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def _save_shares(shares: dict):
    """Save shares to JSON file."""
    shares_file = _get_shares_file()
    shares_file.parent.mkdir(parents=True, exist_ok=True)
    with open(shares_file, "w") as f:
        json.dump(shares, f, indent=2)


@api_router.post("/share", response_model=ShareCreateResponse)
async def create_share(request: ShareCreateRequest):
    """Create a shareable playlist link."""
    songs = _load_songs()
    track_map = {s.get("id"): s for s in songs if s.get("id")}

    tracks = []
    for tid in request.track_ids:
        song = track_map.get(tid)
        if song:
            # Ensure absolute URL
            url = song.get("url")
            if not url and song.get("filename"):
                base_url = str(settings.static_base_url).rstrip("/")
                song["url"] = f"{base_url}/audio/music/{song['filename']}"
            elif url and url.startswith("/"):
                song["url"] = f"https://storage.noahcohn.com{url}"
            tracks.append(song)

    if not tracks:
        raise HTTPException(status_code=400, detail="No valid tracks found for sharing")

    share_id = str(uuid.uuid4())[:12]
    shares = _load_shares()

    # Prune expired shares while we're at it
    now = datetime.now(timezone.utc)
    expired_keys = [
        k for k, v in shares.items()
        if v.get("expires_at") and datetime.fromisoformat(v["expires_at"]) < now
    ]
    for k in expired_keys:
        del shares[k]

    shares[share_id] = {
        "title": request.title,
        "tracks": tracks,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(days=request.expires_in_days or 30)).isoformat(),
    }
    _save_shares(shares)

    full_url = f"https://test.1ink.us/flac-player?share={share_id}"

    return ShareCreateResponse(
        share_id=share_id,
        title=request.title,
        track_count=len(tracks),
        full_url=full_url,
    )


@api_router.get("/share/{share_id}")
async def get_share(share_id: str, request: Request):
    """Retrieve a shared playlist by ID. Returns JSON for API clients,
    or redirects to the FLAC player web app for browsers."""
    shares = _load_shares()
    share = shares.get(share_id)
    if not share:
        raise HTTPException(status_code=404, detail="Share not found")

    # Check expiration
    expires_at = share.get("expires_at")
    if expires_at:
        try:
            expiry = datetime.fromisoformat(expires_at)
            if expiry < datetime.now(timezone.utc):
                raise HTTPException(status_code=410, detail="Share link has expired")
        except ValueError:
            pass

    # Browser redirect — if Accept header prefers HTML, send user to player
    accept = request.headers.get("accept", "")
    if "text/html" in accept and "application/json" not in accept:
        return RedirectResponse(url=f"https://test.1ink.us/flac-player?share={share_id}")

    tracks = share.get("tracks", [])
    # Ensure absolute URLs
    base_url = str(settings.static_base_url).rstrip("/")
    for t in tracks:
        url = t.get("url")
        if not url and t.get("filename"):
            t["url"] = f"{base_url}/audio/music/{t['filename']}"
        elif url and url.startswith("/"):
            t["url"] = f"https://storage.noahcohn.com{url}"

    return ShareGetResponse(title=share.get("title", "Shared Playlist"), tracks=tracks)


# ====================== Playlist Management ======================

class Playlist(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    track_ids: List[str]
    created_at: str
    updated_at: str


class PlaylistCreate(BaseModel):
    title: str
    description: Optional[str] = None
    track_ids: List[str] = []


class PlaylistUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    track_ids: Optional[List[str]] = None


def _get_playlists_file() -> Path:
    """Get the playlists JSON file path."""
    base = Path(settings.files_dir)
    playlists_file = base / "playlists.json"
    return playlists_file


def _load_playlists() -> dict:
    """Load playlists from JSON file."""
    playlists_file = _get_playlists_file()
    if not playlists_file.exists():
        return {}
    try:
        with open(playlists_file, "r") as f:
            data = json.load(f)
            playlists = data.get("playlists", {}) if isinstance(data, dict) else {}
            if isinstance(playlists, list):
                # Normalize legacy list format to dict keyed by id
                return {p.get("id", str(uuid.uuid4())[:12]): p for p in playlists}
            return playlists
    except (json.JSONDecodeError, IOError):
        return {}


def _save_playlists(playlists: dict):
    """Save playlists to JSON file."""
    playlists_file = _get_playlists_file()
    playlists_file.parent.mkdir(parents=True, exist_ok=True)
    with open(playlists_file, "w") as f:
        json.dump({"playlists": playlists}, f, indent=2)


@api_router.get("/playlists", response_model=List[Playlist])
async def list_playlists():
    """List all playlists."""
    playlists = _load_playlists()
    return [
        Playlist(
            id=pid,
            title=p.get("title", "Untitled"),
            description=p.get("description"),
            track_ids=p.get("track_ids", []),
            created_at=p.get("created_at", ""),
            updated_at=p.get("updated_at", "")
        )
        for pid, p in playlists.items()
    ]


@api_router.post("/playlists", response_model=Playlist)
async def create_playlist(request: PlaylistCreate):
    """Create a new playlist."""
    playlists = _load_playlists()
    playlist_id = str(uuid.uuid4())[:12]
    now = datetime.now(timezone.utc).isoformat()
    
    playlists[playlist_id] = {
        "title": request.title,
        "description": request.description,
        "track_ids": request.track_ids or [],
        "created_at": now,
        "updated_at": now
    }
    _save_playlists(playlists)
    
    return Playlist(
        id=playlist_id,
        title=request.title,
        description=request.description,
        track_ids=request.track_ids or [],
        created_at=now,
        updated_at=now
    )


@api_router.get("/playlists/{playlist_id}", response_model=Playlist)
async def get_playlist(playlist_id: str):
    """Get a specific playlist."""
    playlists = _load_playlists()
    p = playlists.get(playlist_id)
    if not p:
        raise HTTPException(status_code=404, detail="Playlist not found")
    
    return Playlist(
        id=playlist_id,
        title=p.get("title", "Untitled"),
        description=p.get("description"),
        track_ids=p.get("track_ids", []),
        created_at=p.get("created_at", ""),
        updated_at=p.get("updated_at", "")
    )


@api_router.patch("/playlists/{playlist_id}", response_model=Playlist)
async def update_playlist(playlist_id: str, request: PlaylistUpdate):
    """Update a playlist."""
    playlists = _load_playlists()
    p = playlists.get(playlist_id)
    if not p:
        raise HTTPException(status_code=404, detail="Playlist not found")
    
    # Update fields if provided
    if request.title is not None:
        p["title"] = request.title
    if request.description is not None:
        p["description"] = request.description
    if request.track_ids is not None:
        p["track_ids"] = request.track_ids
    
    p["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save_playlists(playlists)
    
    return Playlist(
        id=playlist_id,
        title=p.get("title", "Untitled"),
        description=p.get("description"),
        track_ids=p.get("track_ids", []),
        created_at=p.get("created_at", ""),
        updated_at=p.get("updated_at", "")
    )


@api_router.delete("/playlists/{playlist_id}")
async def delete_playlist(playlist_id: str):
    """Delete a playlist."""
    playlists = _load_playlists()
    if playlist_id not in playlists:
        raise HTTPException(status_code=404, detail="Playlist not found")
    
    del playlists[playlist_id]
    _save_playlists(playlists)
    
    return {"success": True, "message": "Playlist deleted"}


@api_router.post("/presets/rescan")
async def rescan_presets():
    """Rebuild cached indexes for all preset directories."""
    counts = await presets.scan_presets()
    return {
        "success": True,
        "dirs": counts,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@api_router.get("/presets/random")
async def get_random_preset(dir: Optional[str] = Query("any")):
    """Return a random preset from the cached index.

    Query param `dir` can be: milk, milkLRG, milkMED, milkSML, custom_milk, or any.
    """
    # Auto-load index on first request if not already in memory
    if not presets._preset_index:
        presets.load_index()

    result = presets.get_random_preset(dir_name=dir if dir != "any" else None)
    if not result:
        raise HTTPException(
            status_code=503,
            detail="Preset index is empty. Run POST /api/presets/rescan first.",
        )
    return result


@api_router.get("/presets/stats")
async def get_preset_stats():
    """Return current preset index statistics."""
    if not presets._preset_index:
        presets.load_index()
    return presets.get_index_stats()


@api_router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "storage-manager-api",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
