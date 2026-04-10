"""Pachinball game content endpoints for maps and music.

This router provides Pachinball-specific content from dedicated storage:
/data/files/pachinball/
  maps/
    maps.json       - Map configurations
  music/
    tracks.json     - Music track metadata
    *.mp3          - Music files
  backbox/
    manifest.json   - Backbox video/image metadata
    *.mp4, *.png   - Media files
  zones/
    manifest.json   - Zone video metadata
    *.mp4          - Zone intro videos
  adventure/
    levels.json     - Adventure level definitions
    progress/       - Player progress (if persisted)
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .config import settings

logger = logging.getLogger(__name__)
pachinball_router = APIRouter(tags=["pachinball"])


# ====================== Models ======================

class MapConfig(BaseModel):
    """Pachinball table map configuration."""
    id: str
    name: str
    baseColor: str = "#00d9ff"
    accentColor: str = "#ffffff"
    glowIntensity: float = 1.0
    backgroundPattern: str = "hex"
    animationSpeed: float = 0.5
    musicTrackId: Optional[str] = None
    shaderUrl: Optional[str] = None
    mode: str = "fixed"  # 'fixed' or 'dynamic'
    worldLength: Optional[int] = 200
    # LCD table specific fields
    scanlineIntensity: float = 0.25
    pixelGridIntensity: float = 0.8
    subpixelIntensity: float = 0.6
    adventureGoals: Optional[List[str]] = None


class MapListResponse(BaseModel):
    maps: List[MapConfig]
    total: int
    source: str = "pachinball"


class MusicTrack(BaseModel):
    """Music track metadata for Pachinball."""
    id: str
    name: str
    title: Optional[str] = None
    artist: str = ""
    url: str
    duration: int = 0
    map_id: Optional[str] = None
    tags: List[str] = []


class MusicListResponse(BaseModel):
    tracks: List[MusicTrack]
    total: int


class BackboxMedia(BaseModel):
    """Backbox media file entry."""
    id: str
    type: str  # 'video' or 'image'
    url: str
    state: str  # 'attract', 'jackpot', 'fever', 'reach', 'adventure'


class BackboxManifestResponse(BaseModel):
    media: List[BackboxMedia]
    version: str = "1.0"


class ZoneVideo(BaseModel):
    """Zone intro video entry."""
    zoneId: str
    name: str
    videoUrl: str
    thumbnailUrl: Optional[str] = None


class ZoneManifestResponse(BaseModel):
    zones: List[ZoneVideo]
    version: str = "1.0"


# ====================== Helpers ======================

def _get_pachinball_dir() -> Path:
    """Get the pachinball storage directory."""
    base = Path(settings.files_dir)
    pachinball_dir = base / "pachinball"
    pachinball_dir.mkdir(parents=True, exist_ok=True)
    return pachinball_dir


def _get_maps_dir() -> Path:
    """Get the maps storage directory."""
    pachinball_dir = _get_pachinball_dir()
    maps_dir = pachinball_dir / "maps"
    maps_dir.mkdir(parents=True, exist_ok=True)
    return maps_dir


def _get_music_dir() -> Path:
    """Get the music storage directory."""
    pachinball_dir = _get_pachinball_dir()
    music_dir = pachinball_dir / "music"
    music_dir.mkdir(parents=True, exist_ok=True)
    return music_dir


def _get_backbox_dir() -> Path:
    """Get the backbox media directory."""
    pachinball_dir = _get_pachinball_dir()
    backbox_dir = pachinball_dir / "backbox"
    backbox_dir.mkdir(parents=True, exist_ok=True)
    return backbox_dir


def _get_zones_dir() -> Path:
    """Get the zones video directory."""
    pachinball_dir = _get_pachinball_dir()
    zones_dir = pachinball_dir / "zones"
    zones_dir.mkdir(parents=True, exist_ok=True)
    return zones_dir


def _load_maps_index() -> List[dict]:
    """Load maps from maps.json."""
    maps_dir = _get_maps_dir()
    maps_file = maps_dir / "maps.json"
    
    if not maps_file.exists():
        return []
    
    try:
        with open(maps_file, "r") as f:
            data = json.load(f)
            return data.get("maps", [])
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Failed to load maps.json: {e}")
        return []


def _load_music_index() -> List[dict]:
    """Load music tracks from tracks.json."""
    music_dir = _get_music_dir()
    tracks_file = music_dir / "tracks.json"
    
    if not tracks_file.exists():
        return []
    
    try:
        with open(tracks_file, "r") as f:
            data = json.load(f)
            return data.get("tracks", [])
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Failed to load tracks.json: {e}")
        return []


def _ensure_absolute_url(url: str, subpath: str) -> str:
    """Ensure a URL is absolute by prepending static_base_url if needed."""
    if url.startswith("http"):
        return url
    base_url = str(settings.static_base_url).rstrip("/")
    # If url starts with /, use it as-is, otherwise prepend subpath
    if url.startswith("/"):
        return f"{base_url}{url}"
    return f"{base_url}/{subpath}/{url}"


# ====================== Default Seed Data ======================

DEFAULT_MAPS = [
    {
        "id": "neon-helix",
        "name": "Neon Helix",
        "baseColor": "#00d9ff",
        "accentColor": "#ffffff",
        "glowIntensity": 1.0,
        "backgroundPattern": "hex",
        "animationSpeed": 0.5,
        "musicTrackId": "neon-helix",
        "shaderUrl": "https://storage.noahcohn.com/pachinball/shaders/neon-helix.glsl",
        "mode": "fixed",
        "worldLength": 200,
        "scanlineIntensity": 0.25,
        "pixelGridIntensity": 0.8,
        "subpixelIntensity": 0.6,
    },
    {
        "id": "cyber-core",
        "name": "Cyber Core",
        "baseColor": "#8800ff",
        "accentColor": "#00d9ff",
        "glowIntensity": 1.1,
        "backgroundPattern": "grid",
        "animationSpeed": 0.6,
        "musicTrackId": "cyber-core",
        "shaderUrl": "https://storage.noahcohn.com/pachinball/shaders/cyber-core.glsl",
        "mode": "fixed",
        "worldLength": 200,
        "scanlineIntensity": 0.3,
        "pixelGridIntensity": 0.9,
        "subpixelIntensity": 0.7,
    },
    {
        "id": "quantum-grid",
        "name": "Quantum Grid",
        "baseColor": "#00ff44",
        "accentColor": "#ffffff",
        "glowIntensity": 1.5,
        "backgroundPattern": "dots",
        "animationSpeed": 0.7,
        "musicTrackId": "quantum-grid",
        "shaderUrl": "https://storage.noahcohn.com/pachinball/shaders/quantum-grid.glsl",
        "mode": "fixed",
        "worldLength": 200,
        "scanlineIntensity": 0.2,
        "pixelGridIntensity": 0.85,
        "subpixelIntensity": 0.65,
    },
]

DEFAULT_TRACKS = [
    {
        "id": "neon-helix",
        "name": "Neon Helix",
        "title": "Neon Helix",
        "artist": "Pachinball",
        "url": "https://storage.noahcohn.com/pachinball/music/neon-helix.mp3",
        "duration": 180,
        "map_id": "neon-helix",
        "tags": ["electronic", "synthwave"],
    },
    {
        "id": "cyber-core",
        "name": "Cyber Core",
        "title": "Cyber Core",
        "artist": "Pachinball",
        "url": "https://storage.noahcohn.com/pachinball/music/cyber-core.mp3",
        "duration": 200,
        "map_id": "cyber-core",
        "tags": ["electronic", "cyberpunk"],
    },
]


def _seed_default_data():
    """Seed default data if files don't exist."""
    maps_dir = _get_maps_dir()
    maps_file = maps_dir / "maps.json"
    
    if not maps_file.exists():
        logger.info("Seeding default pachinball maps")
        with open(maps_file, "w") as f:
            json.dump({"maps": DEFAULT_MAPS, "version": "1.0"}, f, indent=2)
    
    music_dir = _get_music_dir()
    tracks_file = music_dir / "tracks.json"
    
    if not tracks_file.exists():
        logger.info("Seeding default pachinball tracks")
        with open(tracks_file, "w") as f:
            json.dump({"tracks": DEFAULT_TRACKS, "version": "1.0"}, f, indent=2)


# Seed on module load
_seed_default_data()


# ====================== Endpoints ======================

@pachinball_router.get("/maps", response_model=MapListResponse)
async def list_pachinball_maps():
    """List all Pachinball maps from dedicated storage.
    
    Returns maps from /data/files/pachinball/maps/maps.json
    Falls back to seed data if file doesn't exist.
    """
    maps = _load_maps_index()
    
    # Ensure all shader and music URLs are absolute
    base_url = str(settings.static_base_url).rstrip("/")
    for map_config in maps:
        if map_config.get("shaderUrl") and not map_config["shaderUrl"].startswith("http"):
            map_config["shaderUrl"] = f"{base_url}{map_config['shaderUrl']}"
    
    return MapListResponse(
        maps=maps,
        total=len(maps),
        source="pachinball-storage"
    )


@pachinball_router.get("/maps/{map_id}", response_model=MapConfig)
async def get_pachinball_map(map_id: str):
    """Get a specific Pachinball map by ID."""
    maps = _load_maps_index()
    map_config = next((m for m in maps if m.get("id") == map_id), None)
    
    if not map_config:
        raise HTTPException(status_code=404, detail=f"Map not found: {map_id}")
    
    # Ensure shader URL is absolute
    base_url = str(settings.static_base_url).rstrip("/")
    if map_config.get("shaderUrl") and not map_config["shaderUrl"].startswith("http"):
        map_config["shaderUrl"] = f"{base_url}{map_config['shaderUrl']}"
    
    return MapConfig(**map_config)


@pachinball_router.post("/maps")
async def create_pachinball_map(map_config: MapConfig):
    """Create a new Pachinball map.
    
    Adds the map to /data/files/pachinball/maps/maps.json
    """
    maps = _load_maps_index()
    
    # Check for duplicate ID
    if any(m.get("id") == map_config.id for m in maps):
        raise HTTPException(status_code=400, detail=f"Map ID already exists: {map_config.id}")
    
    # Add new map
    maps.append(map_config.model_dump())
    
    # Save
    maps_dir = _get_maps_dir()
    maps_file = maps_dir / "maps.json"
    with open(maps_file, "w") as f:
        json.dump({"maps": maps, "version": "1.0", "updated": datetime.now(timezone.utc).isoformat()}, f, indent=2)
    
    logger.info(f"Created new map: {map_config.id}")
    return {"status": "created", "map": map_config}


@pachinball_router.put("/maps/{map_id}")
async def update_pachinball_map(map_id: str, map_config: MapConfig):
    """Update an existing Pachinball map.
    
    Updates the map in /data/files/pachinball/maps/maps.json
    """
    maps = _load_maps_index()
    
    # Find existing map
    existing_index = next((i for i, m in enumerate(maps) if m.get("id") == map_id), None)
    if existing_index is None:
        raise HTTPException(status_code=404, detail=f"Map not found: {map_id}")
    
    # Update (preserve original ID)
    maps[existing_index] = {**map_config.model_dump(), "id": map_id}
    
    # Save
    maps_dir = _get_maps_dir()
    maps_file = maps_dir / "maps.json"
    with open(maps_file, "w") as f:
        json.dump({"maps": maps, "version": "1.0", "updated": datetime.now(timezone.utc).isoformat()}, f, indent=2)
    
    logger.info(f"Updated map: {map_id}")
    return {"status": "updated", "map": maps[existing_index]}


@pachinball_router.delete("/maps/{map_id}")
async def delete_pachinball_map(map_id: str):
    """Delete a Pachinball map."""
    maps = _load_maps_index()
    
    # Find and remove map
    original_count = len(maps)
    maps = [m for m in maps if m.get("id") != map_id]
    
    if len(maps) == original_count:
        raise HTTPException(status_code=404, detail=f"Map not found: {map_id}")
    
    # Save
    maps_dir = _get_maps_dir()
    maps_file = maps_dir / "maps.json"
    with open(maps_file, "w") as f:
        json.dump({"maps": maps, "version": "1.0", "updated": datetime.now(timezone.utc).isoformat()}, f, indent=2)
    
    logger.info(f"Deleted map: {map_id}")
    return {"status": "deleted", "id": map_id}


@pachinball_router.get("/music", response_model=MusicListResponse)
async def list_pachinball_music():
    """List all Pachinball music tracks from dedicated storage.
    
    Returns tracks from /data/files/pachinball/music/tracks.json
    """
    tracks = _load_music_index()
    
    # Ensure all URLs are absolute
    base_url = str(settings.static_base_url).rstrip("/")
    for track in tracks:
        if track.get("url") and not track["url"].startswith("http"):
            track["url"] = f"{base_url}{track['url']}"
    
    return MusicListResponse(tracks=tracks, total=len(tracks))


@pachinball_router.get("/music/{track_id}", response_model=MusicTrack)
async def get_pachinball_track(track_id: str):
    """Get a specific Pachinball music track by ID."""
    tracks = _load_music_index()
    track = next((t for t in tracks if t.get("id") == track_id), None)
    
    if not track:
        raise HTTPException(status_code=404, detail=f"Track not found: {track_id}")
    
    # Ensure URL is absolute
    base_url = str(settings.static_base_url).rstrip("/")
    if track.get("url") and not track["url"].startswith("http"):
        track["url"] = f"{base_url}{track['url']}"
    
    return MusicTrack(**track)


@pachinball_router.post("/music")
async def create_pachinball_track(track: MusicTrack):
    """Create a new Pachinball music track.
    
    Adds the track to /data/files/pachinball/music/tracks.json
    """
    tracks = _load_music_index()
    
    # Check for duplicate ID
    if any(t.get("id") == track.id for t in tracks):
        raise HTTPException(status_code=400, detail=f"Track ID already exists: {track.id}")
    
    # Add new track
    track_data = track.model_dump()
    track_data["added_at"] = datetime.now(timezone.utc).isoformat()
    tracks.append(track_data)
    
    # Save
    music_dir = _get_music_dir()
    tracks_file = music_dir / "tracks.json"
    with open(tracks_file, "w") as f:
        json.dump({"tracks": tracks, "version": "1.0", "updated": datetime.now(timezone.utc).isoformat()}, f, indent=2)
    
    logger.info(f"Created new track: {track.id}")
    return {"status": "created", "track": track}


@pachinball_router.put("/music/{track_id}")
async def update_pachinball_track(track_id: str, track: MusicTrack):
    """Update an existing Pachinball music track.
    
    Updates the track in /data/files/pachinball/music/tracks.json
    """
    tracks = _load_music_index()
    
    # Find existing track
    existing_index = next((i for i, t in enumerate(tracks) if t.get("id") == track_id), None)
    if existing_index is None:
        raise HTTPException(status_code=404, detail=f"Track not found: {track_id}")
    
    # Update (preserve original ID and added_at)
    existing = tracks[existing_index]
    updated_track = {
        **track.model_dump(),
        "id": track_id,
        "added_at": existing.get("added_at", datetime.now(timezone.utc).isoformat()),
        "updated_at": datetime.now(timezone.utc).isoformat()
    }
    tracks[existing_index] = updated_track
    
    # Save
    music_dir = _get_music_dir()
    tracks_file = music_dir / "tracks.json"
    with open(tracks_file, "w") as f:
        json.dump({"tracks": tracks, "version": "1.0", "updated": datetime.now(timezone.utc).isoformat()}, f, indent=2)
    
    logger.info(f"Updated track: {track_id}")
    return {"status": "updated", "track": updated_track}


@pachinball_router.delete("/music/{track_id}")
async def delete_pachinball_track(track_id: str):
    """Delete a Pachinball music track."""
    tracks = _load_music_index()
    
    # Find and remove track
    original_count = len(tracks)
    tracks = [t for t in tracks if t.get("id") != track_id]
    
    if len(tracks) == original_count:
        raise HTTPException(status_code=404, detail=f"Track not found: {track_id}")
    
    # Save
    music_dir = _get_music_dir()
    tracks_file = music_dir / "tracks.json"
    with open(tracks_file, "w") as f:
        json.dump({"tracks": tracks, "version": "1.0", "updated": datetime.now(timezone.utc).isoformat()}, f, indent=2)
    
    logger.info(f"Deleted track: {track_id}")
    return {"status": "deleted", "id": track_id}


@pachinball_router.get("/backbox", response_model=BackboxManifestResponse)
async def get_backbox_manifest():
    """Get backbox media manifest for attract mode and state videos.
    
    Returns manifest from /data/files/pachinball/backbox/manifest.json
    or generates from available files.
    """
    backbox_dir = _get_backbox_dir()
    manifest_file = backbox_dir / "manifest.json"
    
    base_url = str(settings.static_base_url).rstrip("/")
    media = []
    
    # Try to load manifest if it exists
    if manifest_file.exists():
        try:
            with open(manifest_file, "r") as f:
                data = json.load(f)
                return BackboxManifestResponse(**data)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load backbox manifest: {e}")
    
    # Auto-generate from available files
    state_files = {
        "attract": ["attract.mp4", "attract.webm", "attract.png"],
        "jackpot": ["jackpot.mp4", "jackpot.webm", "jackpot.png"],
        "fever": ["fever.mp4", "fever.webm", "fever.png"],
        "reach": ["reach.mp4", "reach.webm", "reach.png"],
        "adventure": ["adventure.mp4", "adventure.webm", "adventure.png"],
    }
    
    for state, filenames in state_files.items():
        for filename in filenames:
            file_path = backbox_dir / filename
            if file_path.exists():
                file_type = "video" if filename.endswith((".mp4", ".webm")) else "image"
                media.append(BackboxMedia(
                    id=f"{state}-{file_type}",
                    type=file_type,
                    url=f"{base_url}/pachinball/backbox/{filename}",
                    state=state
                ))
    
    return BackboxManifestResponse(media=media)


@pachinball_router.get("/zones", response_model=ZoneManifestResponse)
async def get_zones_manifest():
    """Get zone intro video manifest for adventure mode.
    
    Returns manifest from /data/files/pachinball/zones/manifest.json
    or generates from available files.
    """
    zones_dir = _get_zones_dir()
    manifest_file = zones_dir / "manifest.json"
    
    base_url = str(settings.static_base_url).rstrip("/")
    
    # Try to load manifest if it exists
    if manifest_file.exists():
        try:
            with open(manifest_file, "r") as f:
                data = json.load(f)
                # Ensure all video URLs are absolute
                for zone in data.get("zones", []):
                    if zone.get("videoUrl") and not zone["videoUrl"].startswith("http"):
                        zone["videoUrl"] = f"{base_url}{zone['videoUrl']}"
                return ZoneManifestResponse(**data)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load zones manifest: {e}")
    
    # Auto-generate from available video files
    zones = []
    zone_mapping = {
        "neon_helix_intro": ("neon-helix", "Neon Helix"),
        "cyber_core_intro": ("cyber-core", "Cyber Core"),
        "quantum_grid_intro": ("quantum-grid", "Quantum Grid"),
        "singularity_intro": ("singularity-well", "Singularity Well"),
        "glitch_spire_intro": ("glitch-spire", "Glitch Spire"),
    }
    
    for filename, (zone_id, zone_name) in zone_mapping.items():
        for ext in [".mp4", ".webm"]:
            file_path = zones_dir / f"{filename}{ext}"
            if file_path.exists():
                zones.append(ZoneVideo(
                    zoneId=zone_id,
                    name=zone_name,
                    videoUrl=f"{base_url}/pachinball/zones/{filename}{ext}"
                ))
                break
    
    return ZoneManifestResponse(zones=zones)


@pachinball_router.get("/health")
async def pachinball_health_check():
    """Health check for Pachinball content endpoints."""
    pachinball_dir = _get_pachinball_dir()
    
    # Check what content is available
    maps_dir = _get_maps_dir()
    music_dir = _get_music_dir()
    backbox_dir = _get_backbox_dir()
    zones_dir = _get_zones_dir()
    
    maps_count = len(_load_maps_index())
    tracks_count = len(_load_music_index())
    
    return {
        "status": "healthy",
        "service": "pachinball-content",
        "storage_path": str(pachinball_dir),
        "content": {
            "maps": maps_count,
            "tracks": tracks_count,
            "has_backbox": backbox_dir.exists() and any(backbox_dir.iterdir()),
            "has_zones": zones_dir.exists() and any(zones_dir.iterdir()),
        },
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


# ====================== File Upload Endpoints ======================

@pachinball_router.post("/upload/music")
async def upload_music_file(
    file: UploadFile = File(...),
    track_id: str = Form(...),
    title: str = Form(...),
    artist: str = Form("Unknown"),
    map_id: Optional[str] = Form(None),
):
    """Upload a music file (MP3, OGG, WAV) for Pachinball.
    
    Args:
        file: The audio file to upload
        track_id: Unique track ID (e.g., 'neon-helix')
        title: Song title
        artist: Artist name
        map_id: Associated map ID (optional)
    
    Returns:
        The created track metadata
    """
    # Validate file extension
    filename = file.filename or ""
    ext = Path(filename).suffix.lower()
    allowed_exts = ['.mp3', '.ogg', '.wav', '.flac', '.m4a']
    
    if ext not in allowed_exts:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type. Allowed: {', '.join(allowed_exts)}"
        )
    
    # Get music directory
    music_dir = _get_music_dir()
    
    # Generate safe filename
    safe_filename = f"{track_id}{ext}"
    file_path = music_dir / safe_filename
    
    # Read and save file
    content = await file.read()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {settings.max_upload_mb} MB limit"
        )
    
    # Write file
    with open(file_path, "wb") as f:
        f.write(content)
    
    # Add to tracks index
    tracks = _load_music_index()
    
    # Remove existing entry with same ID if present
    tracks = [t for t in tracks if t.get("id") != track_id]
    
    # Create new track entry
    track_data = {
        "id": track_id,
        "name": title,
        "title": title,
        "artist": artist,
        "url": f"/pachinball/music/{safe_filename}",
        "duration": 0,  # Could extract from metadata
        "map_id": map_id,
        "tags": [],
        "added_at": datetime.now(timezone.utc).isoformat(),
        "size": len(content)
    }
    tracks.append(track_data)
    
    # Save tracks index
    tracks_file = music_dir / "tracks.json"
    with open(tracks_file, "w") as f:
        json.dump({"tracks": tracks, "version": "1.0", "updated": datetime.now(timezone.utc).isoformat()}, f, indent=2)
    
    logger.info(f"Uploaded music file: {safe_filename} ({len(content)} bytes)")
    return {
        "status": "uploaded",
        "track": track_data,
        "filename": safe_filename
    }


@pachinball_router.post("/upload/backbox")
async def upload_backbox_file(
    file: UploadFile = File(...),
    state: str = Form(...),  # attract, jackpot, fever, reach, adventure
    file_type: str = Form(...),  # video, image
):
    """Upload a backbox media file (video or image).
    
    Args:
        file: The media file to upload (MP4, WEBM, PNG, JPG)
        state: Which state this file is for (attract, jackpot, fever, reach, adventure)
        file_type: Type of media (video, image)
    
    Returns:
        Upload status and file URL
    """
    # Validate state
    valid_states = ["attract", "jackpot", "fever", "reach", "adventure"]
    if state not in valid_states:
        raise HTTPException(status_code=400, detail=f"Invalid state. Must be one of: {', '.join(valid_states)}")
    
    # Validate file extension
    filename = file.filename or ""
    ext = Path(filename).suffix.lower()
    
    if file_type == "video":
        allowed_exts = ['.mp4', '.webm', '.mov']
    else:
        allowed_exts = ['.png', '.jpg', '.jpeg', '.gif', '.webp']
    
    if ext not in allowed_exts:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type for {file_type}. Allowed: {', '.join(allowed_exts)}"
        )
    
    # Get backbox directory
    backbox_dir = _get_backbox_dir()
    
    # Generate filename based on state and type
    safe_filename = f"{state}{ext}"
    file_path = backbox_dir / safe_filename
    
    # Read and save file
    content = await file.read()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {settings.max_upload_mb} MB limit"
        )
    
    # Write file
    with open(file_path, "wb") as f:
        f.write(content)
    
    logger.info(f"Uploaded backbox file: {safe_filename} ({len(content)} bytes)")
    return {
        "status": "uploaded",
        "state": state,
        "file_type": file_type,
        "filename": safe_filename,
        "url": f"/pachinball/backbox/{safe_filename}",
        "size": len(content)
    }


@pachinball_router.post("/upload/zone")
async def upload_zone_video(
    file: UploadFile = File(...),
    zone_id: str = Form(...),
    zone_name: str = Form(...),
):
    """Upload a zone intro video.
    
    Args:
        file: The video file to upload (MP4, WEBM)
        zone_id: Zone ID (e.g., 'neon-helix')
        zone_name: Display name for the zone
    
    Returns:
        Upload status and file URL
    """
    # Validate file extension
    filename = file.filename or ""
    ext = Path(filename).suffix.lower()
    allowed_exts = ['.mp4', '.webm', '.mov']
    
    if ext not in allowed_exts:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type. Allowed: {', '.join(allowed_exts)}"
        )
    
    # Get zones directory
    zones_dir = _get_zones_dir()
    
    # Generate safe filename (convert zone_id to snake_case)
    safe_id = zone_id.replace("-", "_").lower()
    safe_filename = f"{safe_id}_intro{ext}"
    file_path = zones_dir / safe_filename
    
    # Read and save file
    content = await file.read()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {settings.max_upload_mb} MB limit"
        )
    
    # Write file
    with open(file_path, "wb") as f:
        f.write(content)
    
    # Update zones manifest
    manifest_file = zones_dir / "manifest.json"
    zones = []
    if manifest_file.exists():
        try:
            with open(manifest_file, "r") as f:
                data = json.load(f)
                zones = data.get("zones", [])
        except (json.JSONDecodeError, IOError):
            pass
    
    # Remove existing entry with same zone_id if present
    zones = [z for z in zones if z.get("zoneId") != zone_id]
    
    # Add new zone entry
    zones.append({
        "zoneId": zone_id,
        "name": zone_name,
        "videoUrl": f"/pachinball/zones/{safe_filename}",
        "thumbnailUrl": f"/pachinball/zones/{safe_id}_thumb.png"
    })
    
    # Save manifest
    with open(manifest_file, "w") as f:
        json.dump({"zones": zones, "version": "1.0", "updated": datetime.now(timezone.utc).isoformat()}, f, indent=2)
    
    logger.info(f"Uploaded zone video: {safe_filename} ({len(content)} bytes)")
    return {
        "status": "uploaded",
        "zone_id": zone_id,
        "zone_name": zone_name,
        "filename": safe_filename,
        "url": f"/pachinball/zones/{safe_filename}",
        "size": len(content)
    }


@pachinball_router.get("/files/{file_path:path}")
async def serve_pachinball_file(file_path: str):
    """Serve a static file from the pachinball storage directory."""
    pachinball_dir = _get_pachinball_dir()
    full_path = pachinball_dir / file_path
    
    # Security check: ensure file is within pachinball directory
    try:
        full_path.resolve().relative_to(pachinball_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")
    
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    
    if not full_path.is_file():
        raise HTTPException(status_code=400, detail="Not a file")
    
    # Determine media type
    ext = full_path.suffix.lower()
    media_types = {
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".mov": "video/quicktime",
        ".mp3": "audio/mpeg",
        ".ogg": "audio/ogg",
        ".wav": "audio/wav",
        ".flac": "audio/flac",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".glsl": "text/plain",
        ".json": "application/json",
    }
    media_type = media_types.get(ext, "application/octet-stream")
    
    return FileResponse(full_path, media_type=media_type)
