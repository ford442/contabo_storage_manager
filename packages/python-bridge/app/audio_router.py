"""Audio endpoints for music and sound samples."""

import json
import logging
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

import asyncio
from .flac_client import register_song_with_flac_player

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from .config import settings

logger = logging.getLogger(__name__)
audio_router = APIRouter(prefix="/api", tags=["audio"])

# ── Song directory constants ───────────────────────────────────────────────────

SONG_DIRS: tuple[str, ...] = ("songs", "weeks_songs", "flac_songs")

SONG_EXTENSIONS: frozenset[str] = frozenset(
    {".mp3", ".flac", ".wav", ".ogg", ".m4a", ".opus"}
)

_SONG_MIME_TYPES: dict[str, str] = {
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    ".opus": "audio/opus",
}

# Static mapping built once at module load — never derive paths from raw user input.
_SONG_DIR_MAP: dict[str, Path] = {
    name: Path(settings.songs_dir) / name for name in SONG_DIRS
}


# ── Song directory Pydantic models ────────────────────────────────────────────


class SongDirInfo(BaseModel):
    name: str
    count: int
    updated_at: str | None


class SongFileMeta(BaseModel):
    name: str
    size: int
    modified_at: str
    url: str  # full URL: {settings.static_base_url}/songs/dirs/{dir_name}/{filename}


# ── Song directory helpers ─────────────────────────────────────────────────────


def _iso_utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).isoformat()


def _validate_song_dir(dir_name: str) -> None:
    """Raise HTTP 400 if dir_name is not in the allowed whitelist."""
    if dir_name not in SONG_DIRS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown song directory '{dir_name}'. "
            f"Allowed: {', '.join(SONG_DIRS)}",
        )


def _song_dir(dir_name: str) -> Path:
    """Return (and auto-create) the path for a whitelisted song directory."""
    if dir_name not in _SONG_DIR_MAP:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown song directory '{dir_name}'. "
            f"Allowed: {', '.join(SONG_DIRS)}",
        )
    d = _SONG_DIR_MAP[dir_name]
    d.mkdir(parents=True, exist_ok=True)
    return d


def _validate_song_filename(filename: str) -> None:
    """Raise HTTP 400 if the filename is unsafe or not an allowed audio type."""
    if not filename:
        raise HTTPException(status_code=400, detail="Filename is required.")
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Path traversal is not allowed.")
    suffix = Path(filename).suffix.lower()
    if suffix not in SONG_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{suffix}'. "
            f"Allowed: {', '.join(sorted(SONG_EXTENSIONS))}",
        )
    blocked_chars = set(
        "\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f"
        "\x10\x11\x12\x13\x14\x15\x16\x17\x18\x19\x1a\x1b\x1c\x1d\x1e\x1f"
        "\x7f<>:\"|?*"
    )
    if any(c in blocked_chars for c in filename):
        raise HTTPException(
            status_code=400,
            detail="Filename contains invalid characters.",
        )


def _song_file_path(dir_name: str, filename: str) -> Path:
    """Return the resolved path for a song file, confined to its directory."""
    song_dir = _song_dir(dir_name)
    candidate = (song_dir / filename).resolve()
    if not candidate.is_relative_to(song_dir.resolve()):
        raise HTTPException(status_code=400, detail="Path traversal is not allowed.")
    return candidate


def _song_file_url(dir_name: str, filename: str) -> str:
    base = settings.static_base_url.rstrip("/")
    return f"{base}/songs/dirs/{dir_name}/{filename}"


def _parse_range_header(range_header: str, file_size: int) -> Tuple[int, int]:
    """Parse HTTP Range header, returns (start, end) byte positions."""
    try:
        if not range_header.startswith("bytes="):
            raise ValueError("Invalid range unit")
        range_spec = range_header[6:]
        if "-" not in range_spec:
            raise ValueError("Invalid range format")
        start_str, end_str = range_spec.split("-", 1)
        if start_str == "" and end_str != "":
            end_val = int(end_str)
            start = max(0, file_size - end_val)
            end = file_size - 1
        elif start_str != "" and end_str == "":
            start = int(start_str)
            end = file_size - 1
        elif start_str != "" and end_str != "":
            start = int(start_str)
            end = min(int(end_str), file_size - 1)
        else:
            raise ValueError("Invalid range format")
        if start < 0 or start >= file_size:
            raise ValueError("Range start out of bounds")
        if end < start:
            raise ValueError("Range end before start")
        return start, end
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=416, detail=f"Range not satisfiable: {e}")


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


@audio_router.get("/music/tracks/{track_id}")
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
    
    # Try different audio formats, including FLAC
    # Files are named {track_id}_{name}.{ext}
    for ext in [".flac", ".mp3", ".ogg", ".wav", ".m4a", ".opus"]:
        # First try exact match
        file_path = music_dir / f"{track_id}{ext}"
        if file_path.exists():
            return FileResponse(
                file_path,
                media_type=_SONG_MIME_TYPES.get(ext, f"audio/{ext[1:]}"),
                filename=f"{track_id}{ext}"
            )
        
        # Then try with wildcard pattern (track_id_*.ext)
        matches = list(music_dir.glob(f"{track_id}_*{ext}"))
        if matches:
            file_path = matches[0]
            return FileResponse(
                file_path,
                media_type=_SONG_MIME_TYPES.get(ext, f"audio/{ext[1:]}"),
                filename=file_path.name
            )
    
    raise HTTPException(status_code=404, detail="Audio file not found")

@audio_router.get("/music/{track_id}")
async def get_music_file_alias(track_id: str):
    """Alias to stream a music track file directly by ID.
    
    The FLAC player frontend expects audio files at /api/music/{track_id} 
    without the /file suffix. This alias fulfills that request.
    """
    return await get_music_file(track_id)

@audio_router.post("/music")
async def add_music_track(track: MusicTrack):
    """Add a new music track to the index and notify the FLAC player."""
    tracks = _load_music_index()
    
    # Check for duplicate ID
    if any(t["id"] == track.id for t in tracks):
        raise HTTPException(status_code=400, detail="Track ID already exists")
    
    track_data = track.model_dump()
    track_data["added_at"] = datetime.now(timezone.utc).isoformat()
    tracks.append(track_data)
    
    _save_music_index(tracks)
    
    # --- NEW: Trigger FLAC Player Webhook ---
    # Construct the file name and URL based on your existing URL patterns
    filename = f"{track.id}.mp3" 
    public_url = track.url if track.url else f"{settings.static_base_url}/audio/music/{filename}"
    
    # Run the webhook in the background so it doesn't block the API response
    asyncio.create_task(
        register_song_with_flac_player(
            filename=filename,
            public_url=public_url,
            title=track.title,
            author=track.artist if track.artist else "Noah",
            tags=track.tags,
            duration=track.duration,
            song_id=track.id
        )
    )
    # ----------------------------------------
    
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
        # First try exact match
        file_path = samples_dir / f"{sample_id}{ext}"
        if file_path.exists():
            return FileResponse(
                file_path,
                media_type=f"audio/{ext[1:]}",
                filename=f"{sample_id}{ext}"
            )
        
        # Then try with wildcard pattern (sample_id_*.ext)
        matches = list(samples_dir.glob(f"{sample_id}_*{ext}"))
        if matches:
            file_path = matches[0]
            return FileResponse(
                file_path,
                media_type=f"audio/{ext[1:]}",
                filename=file_path.name
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


# ── Song directory listing endpoints ──────────────────────────────────────────


@audio_router.get("/songs/dirs", response_model=list[SongDirInfo])
async def list_song_dirs():
    """List all song directories with file counts and latest modification time."""
    results: list[SongDirInfo] = []
    for name in SONG_DIRS:
        d = _song_dir(name)
        files = [f for f in d.iterdir() if f.is_file() and f.suffix.lower() in SONG_EXTENSIONS]
        count = len(files)
        updated_at: str | None = None
        if files:
            latest = max(f.stat().st_mtime for f in files)
            updated_at = _iso_utc(latest)
        results.append(SongDirInfo(name=name, count=count, updated_at=updated_at))
    return results


@audio_router.get("/songs/dirs/{dir_name}", response_model=list[SongFileMeta])
async def list_song_dir_files(dir_name: str):
    """List audio files in a song directory, sorted by name."""
    _validate_song_dir(dir_name)
    d = _song_dir(dir_name)
    files = sorted(
        (f for f in d.iterdir() if f.is_file() and f.suffix.lower() in SONG_EXTENSIONS),
        key=lambda x: x.name.lower(),
    )
    entries: list[SongFileMeta] = []
    for p in files:
        stat = p.stat()
        entries.append(
            SongFileMeta(
                name=p.name,
                size=stat.st_size,
                modified_at=_iso_utc(stat.st_mtime),
                url=_song_file_url(dir_name, p.name),
            )
        )
    return entries


@audio_router.head("/songs/dirs/{dir_name}/{filename}")
async def head_song_dir_file(dir_name: str, filename: str):
    """Return headers for a song file without the body."""
    _validate_song_dir(dir_name)
    _validate_song_filename(filename)
    path = _song_file_path(dir_name, filename)
    if not path.exists():
        raise HTTPException(
            status_code=404, detail=f"Song '{filename}' not found in '{dir_name}'."
        )
    media_type = _SONG_MIME_TYPES.get(path.suffix.lower(), "application/octet-stream")
    file_size = path.stat().st_size
    return Response(
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
            "Content-Type": media_type,
            "Cache-Control": "public, max-age=3600",
        },
        status_code=200,
    )


@audio_router.get("/songs/dirs/{dir_name}/{filename}")
async def serve_song_dir_file(
    dir_name: str,
    filename: str,
    range: Optional[str] = Header(None),
):
    """Serve a raw audio file from a song directory with HTTP range request support.

    Range support allows the browser's HTMLAudioElement to stream audio
    incrementally instead of downloading the full file before playback.
    """
    _validate_song_dir(dir_name)
    _validate_song_filename(filename)
    path = _song_file_path(dir_name, filename)
    if not path.exists():
        raise HTTPException(
            status_code=404, detail=f"Song '{filename}' not found in '{dir_name}'."
        )
    media_type = _SONG_MIME_TYPES.get(path.suffix.lower(), "application/octet-stream")
    file_size = path.stat().st_size

    if range:
        start, end = _parse_range_header(range, file_size)
        content_length = end - start + 1

        def iter_range():
            with open(path, "rb") as f:
                f.seek(start)
                remaining = content_length
                chunk_size = 64 * 1024
                while remaining > 0:
                    chunk = f.read(min(chunk_size, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        return StreamingResponse(
            iter_range(),
            status_code=206,
            media_type=media_type,
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(content_length),
                "Cache-Control": "public, max-age=3600",
            },
        )

    # Full file — still advertise range support so the browser knows it can seek
    def iter_full():
        with open(path, "rb") as f:
            while chunk := f.read(256 * 1024):
                yield chunk

    return StreamingResponse(
        iter_full(),
        media_type=media_type,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
            "Cache-Control": "public, max-age=3600",
        },
    )
