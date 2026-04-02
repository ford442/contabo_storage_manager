"""Audio endpoints for music and sound samples."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .config import settings

logger = logging.getLogger(__name__)
audio_router = APIRouter(prefix="/api", tags=["audio"])


# ====================== Models ======================

class MusicTrack(BaseModel):
    id: str
    title: str
    artist: str = ""
    url: str
    duration: int = 0
    map_id: Optional[str] = None  # Associated map (1-8, M, etc.)
    tags: List[str] = []


class SoundSample(BaseModel):
    id: str
    name: str
    url: str
    category: str = ""  # "peg", "bumper", "flipper", "jackpot", etc.
    duration: float = 0.0


class MusicListResponse(BaseModel):
    tracks: List[MusicTrack]
    total: int


class SamplesListResponse(BaseModel):
    samples: List[SoundSample]
    total: int


# ====================== Helpers ======================

def _get_audio_dir() -> Path:
    """Get the audio storage directory."""
    base = Path(settings.files_dir)
    audio_dir = base / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    return audio_dir


def _get_music_dir() -> Path:
    """Get the music storage directory."""
    audio_dir = _get_audio_dir()
    music_dir = audio_dir / "music"
    music_dir.mkdir(parents=True, exist_ok=True)
    return music_dir


def _get_samples_dir() -> Path:
    """Get the samples storage directory."""
    audio_dir = _get_audio_dir()
    samples_dir = audio_dir / "samples"
    samples_dir.mkdir(parents=True, exist_ok=True)
    return samples_dir


def _load_music_index() -> List[dict]:
    """Load the music index file."""
    music_dir = _get_music_dir()
    index_file = music_dir / "index.json"
    
    if not index_file.exists():
        # Create default empty index
        return []
    
    try:
        with open(index_file, "r") as f:
            data = json.load(f)
            return data.get("tracks", [])
    except (json.JSONDecodeError, IOError):
        return []


def _save_music_index(tracks: List[dict]):
    """Save the music index file."""
    music_dir = _get_music_dir()
    index_file = music_dir / "index.json"
    
    with open(index_file, "w") as f:
        json.dump({"tracks": tracks, "updated": datetime.now(timezone.utc).isoformat()}, f, indent=2)


def _load_samples_index() -> List[dict]:
    """Load the samples index file."""
    samples_dir = _get_samples_dir()
    index_file = samples_dir / "index.json"
    
    if not index_file.exists():
        return []
    
    try:
        with open(index_file, "r") as f:
            data = json.load(f)
            return data.get("samples", [])
    except (json.JSONDecodeError, IOError):
        return []


def _save_samples_index(samples: List[dict]):
    """Save the samples index file."""
    samples_dir = _get_samples_dir()
    index_file = samples_dir / "index.json"
    
    with open(index_file, "w") as f:
        json.dump({"samples": samples, "updated": datetime.now(timezone.utc).isoformat()}, f, indent=2)


# ====================== Endpoints ======================

@audio_router.get("/music", response_model=MusicListResponse)
async def list_music(
    map_id: Optional[str] = Query(None, description="Filter by map ID (1-8, M)"),
    tag: Optional[str] = Query(None, description="Filter by tag")
):
    """List all music tracks with optional filtering."""
    tracks = _load_music_index()
    
    # Apply filters
    if map_id:
        tracks = [t for t in tracks if t.get("map_id") == map_id]
    if tag:
        tracks = [t for t in tracks if tag in t.get("tags", [])]
    
    # Add full URLs
    base_url = settings.static_base_url
    for track in tracks:
        track["url"] = f"{base_url}/audio/music/{track['id']}.mp3"
    
    return MusicListResponse(tracks=tracks, total=len(tracks))


@audio_router.get("/music/{track_id}")
async def get_music_track(track_id: str):
    """Get a specific music track's metadata."""
    tracks = _load_music_index()
    track = next((t for t in tracks if t["id"] == track_id), None)
    
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")
    
    base_url = settings.static_base_url
    track["url"] = f"{base_url}/audio/music/{track_id}.mp3"
    
    return track


@audio_router.get("/music/{track_id}/file")
async def get_music_file(track_id: str):
    """Stream a music track file."""
    music_dir = _get_music_dir()
    
    # Try different audio formats
    for ext in [".mp3", ".ogg", ".wav", ".m4a"]:
        file_path = music_dir / f"{track_id}{ext}"
        if file_path.exists():
            return FileResponse(
                file_path,
                media_type=f"audio/{ext[1:]}",
                filename=f"{track_id}{ext}"
            )
    
    raise HTTPException(status_code=404, detail="Audio file not found")


@audio_router.post("/music")
async def add_music_track(track: MusicTrack):
    """Add a new music track to the index."""
    tracks = _load_music_index()
    
    # Check for duplicate ID
    if any(t["id"] == track.id for t in tracks):
        raise HTTPException(status_code=400, detail="Track ID already exists")
    
    track_data = track.model_dump()
    track_data["added_at"] = datetime.now(timezone.utc).isoformat()
    tracks.append(track_data)
    
    _save_music_index(tracks)
    
    return {"status": "added", "track": track_data}


@audio_router.get("/samples", response_model=SamplesListResponse)
async def list_samples(
    category: Optional[str] = Query(None, description="Filter by category (peg, bumper, flipper, jackpot)"),
    tag: Optional[str] = Query(None, description="Filter by tag")
):
    """List all sound samples with optional filtering."""
    samples = _load_samples_index()
    
    # Apply filters
    if category:
        samples = [s for s in samples if s.get("category") == category]
    if tag:
        samples = [s for s in samples if tag in s.get("tags", [])]
    
    # Add full URLs
    base_url = settings.static_base_url
    for sample in samples:
        sample["url"] = f"{base_url}/audio/samples/{sample['id']}.mp3"
    
    return SamplesListResponse(samples=samples, total=len(samples))


@audio_router.get("/samples/{sample_id}")
async def get_sample(sample_id: str):
    """Get a specific sample's metadata."""
    samples = _load_samples_index()
    sample = next((s for s in samples if s["id"] == sample_id), None)
    
    if not sample:
        raise HTTPException(status_code=404, detail="Sample not found")
    
    base_url = settings.static_base_url
    sample["url"] = f"{base_url}/audio/samples/{sample_id}.mp3"
    
    return sample


@audio_router.get("/samples/{sample_id}/file")
async def get_sample_file(sample_id: str):
    """Stream a sample file."""
    samples_dir = _get_samples_dir()
    
    # Try different audio formats
    for ext in [".mp3", ".ogg", ".wav", ".m4a"]:
        file_path = samples_dir / f"{sample_id}{ext}"
        if file_path.exists():
            return FileResponse(
                file_path,
                media_type=f"audio/{ext[1:]}",
                filename=f"{sample_id}{ext}"
            )
    
    raise HTTPException(status_code=404, detail="Sample file not found")


@audio_router.post("/samples")
async def add_sample(sample: SoundSample):
    """Add a new sound sample to the index."""
    samples = _load_samples_index()
    
    # Check for duplicate ID
    if any(s["id"] == sample.id for s in samples):
        raise HTTPException(status_code=400, detail="Sample ID already exists")
    
    sample_data = sample.model_dump()
    sample_data["added_at"] = datetime.now(timezone.utc).isoformat()
    samples.append(sample_data)
    
    _save_samples_index(samples)
    
    return {"status": "added", "sample": sample_data}


@audio_router.get("/samples/random/{category}")
async def get_random_sample(category: str):
    """Get a random sample from a category."""
    import random
    
    samples = _load_samples_index()
    category_samples = [s for s in samples if s.get("category") == category]
    
    if not category_samples:
        raise HTTPException(status_code=404, detail=f"No samples found in category: {category}")
    
    sample = random.choice(category_samples)
    base_url = settings.static_base_url
    sample["url"] = f"{base_url}/audio/samples/{sample['id']}.mp3"
    
    return sample
