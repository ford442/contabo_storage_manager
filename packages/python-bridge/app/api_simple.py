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


class ShaderRatingUpdate(BaseModel):
    rating: int = Field(..., ge=0, le=5, description="Rating 0-5 stars. 0 = has errors")
    notes: Optional[str] = None


class ImageRecord(BaseModel):
    url: str
    description: Optional[str] = None
    tags: List[str] = []


class SongRecord(BaseModel):
    id: str
    title: str
    artist: str = ""
    url: str
    duration: Optional[int] = None
    type: str = "audio"


# ====================== Helper Functions ======================

def _get_shaders_dir() -> Path:
    """Get the shaders directory."""
    return Path(settings.files_dir) / "image-effects" / "shaders"

def _get_metadata_dir() -> Path:
    """Get the metadata directory."""
    return Path(settings.files_dir) / "image-effects" / "metadata"

def _get_ratings_file() -> Path:
    """Get the ratings database file."""
    metadata_dir = _get_metadata_dir()
    metadata_dir.mkdir(parents=True, exist_ok=True)
    return metadata_dir / "shader_ratings.json"

def _load_ratings() -> dict:
    """Load the ratings database."""
    ratings_file = _get_ratings_file()
    if ratings_file.exists():
        try:
            return json.loads(ratings_file.read_text())
        except (json.JSONDecodeError, IOError):
            return {}
    return {}

def _save_ratings(ratings: dict):
    """Save the ratings database."""
    ratings_file = _get_ratings_file()
    ratings_file.write_text(json.dumps(ratings, indent=2))


# ====================== Shader API Endpoints ======================

@api_router.get("/shaders", response_model=List[ShaderMetadata])
async def list_shaders():
    """List all available shaders with metadata and ratings."""
    shaders_dir = _get_shaders_dir()
    ratings = _load_ratings()
    
    shaders = []
    if shaders_dir.exists():
        for file_path in sorted(shaders_dir.glob("*.json")):
            try:
                data = json.loads(file_path.read_text())
                shader_id = file_path.stem
                
                # Get rating from ratings database
                rating_data = ratings.get(shader_id, {})
                rating = rating_data.get("rating")
                has_errors = rating == 0
                
                metadata = ShaderMetadata(
                    id=shader_id,
                    name=data.get("name", shader_id),
                    author=data.get("author", ""),
                    date=data.get("date", ""),
                    description=data.get("description", ""),
                    filename=file_path.name,
                    tags=data.get("tags", []),
                    rating=rating,
                    source=data.get("source", "upload"),
                    format=data.get("format", "wgsl"),
                    has_errors=has_errors
                )
                shaders.append(metadata)
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Failed to parse shader {file_path}: {e}")
    
    return shaders


@api_router.get("/shaders/{shader_id}", response_model=dict)
async def get_shader(shader_id: str):
    """Get a shader by ID."""
    shaders_dir = _get_shaders_dir()
    
    # Try different filename patterns
    patterns = [
        f"{shader_id}.json",
        f"*{shader_id}*.json",
    ]
    
    for pattern in patterns:
        matches = list(shaders_dir.glob(pattern))
        if matches:
            try:
                data = json.loads(matches[0].read_text())
                return {
                    "id": shader_id,
                    "content": json.dumps(data),
                    "type": data.get("format", "wgsl"),
                    "data": data
                }
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Failed to read shader {shader_id}: {e}")
                raise HTTPException(status_code=500, detail="Failed to read shader")
    
    raise HTTPException(status_code=404, detail="Shader not found")


@api_router.post("/shaders")
async def create_shader(shader_data: dict):
    """Create or update a shader with metadata and WGSL code."""
    shaders_dir = _get_shaders_dir()
    metadata_dir = _get_metadata_dir()
    
    try:
        # Extract data
        shader_id = shader_data.get("id") or shader_data.get("name", "unknown")
        name = shader_data.get("name", shader_id)
        wgsl_code = shader_data.get("wgsl_code") or shader_data.get("code", "")
        
        # Sanitize filename
        safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in shader_id)
        filename = f"{safe_name}.json"
        filepath = shaders_dir / filename
        
        # Build metadata
        metadata = {
            "id": shader_id,
            "name": name,
            "author": shader_data.get("author", ""),
            "date": shader_data.get("date", datetime.now(timezone.utc).isoformat()),
            "type": "shader",
            "description": shader_data.get("description", ""),
            "filename": filename,
            "tags": shader_data.get("tags", []),
            "format": shader_data.get("format", "wgsl"),
            "source": shader_data.get("source", "upload"),
            "coordinate": shader_data.get("coordinate"),
            "category": shader_data.get("category", "image"),
            "features": shader_data.get("features", []),
        }
        
        # Save metadata JSON
        filepath.write_text(json.dumps(metadata, indent=2))
        
        # Save WGSL code separately
        if wgsl_code:
            wgsl_path = shaders_dir / f"{safe_name}.wgsl"
            wgsl_path.write_text(wgsl_code)
        
        logger.info(f"Created shader: {shader_id} at {filepath}")
        
        return {
            "success": True,
            "id": shader_id,
            "filename": filename,
            "message": f"Shader '{name}' created successfully",
            "url": f"/files/image-effects/shaders/{filename}"
        }
        
    except Exception as e:
        logger.error(f"Failed to create shader: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create shader: {str(e)}")


@api_router.post("/shaders/{shader_id}/rate")
async def rate_shader(shader_id: str, rating_update: ShaderRatingUpdate):
    """Rate a shader (0-5 stars). 0 stars marks the shader as having errors."""
    ratings = _load_ratings()
    
    ratings[shader_id] = {
        "rating": rating_update.rating,
        "notes": rating_update.notes,
        "date": datetime.now(timezone.utc).isoformat()
    }
    
    _save_ratings(ratings)
    
    status = "errors" if rating_update.rating == 0 else "rated"
    return {
        "success": True,
        "id": shader_id,
        "rating": rating_update.rating,
        "status": status,
        "message": f"Shader marked as {status}"
    }


@api_router.get("/shaders/{shader_id}/rating")
async def get_shader_rating(shader_id: str):
    """Get the rating for a specific shader."""
    ratings = _load_ratings()
    rating_data = ratings.get(shader_id, {})
    
    if not rating_data:
        raise HTTPException(status_code=404, detail="No rating found for this shader")
    
    return {
        "id": shader_id,
        "rating": rating_data.get("rating"),
        "notes": rating_data.get("notes"),
        "date": rating_data.get("date"),
        "has_errors": rating_data.get("rating") == 0
    }


@api_router.get("/shaders/errors", response_model=List[ShaderMetadata])
async def list_shaders_with_errors():
    """List all shaders marked with 0 stars (errors)."""
    all_shaders = await list_shaders()
    return [s for s in all_shaders if s.has_errors or s.rating == 0]


# ====================== Songs/Images API Endpoints ======================

@api_router.get("/songs")
async def list_songs(
    type: Optional[str] = Query(None, description="Filter by type: audio, image, video")
):
    """List available media files (songs, images, videos)."""
    results = []
    
    # Images from outputs
    if type is None or type == "image":
        images_dir = Path(settings.files_dir) / "image-effects" / "outputs"
        if images_dir.exists():
            for date_dir in sorted(images_dir.iterdir()):
                if date_dir.is_dir():
                    for img_file in date_dir.glob("*"):
                        if img_file.suffix.lower() in [".jpg", ".jpeg", ".png", ".webp", ".gif"]:
                            results.append(ImageRecord(
                                url=f"/files/image-effects/outputs/{date_dir.name}/{img_file.name}",
                                description=f"Generated image from {date_dir.name}",
                                tags=["generated", date_dir.name]
                            ))
    
    # Audio files
    if type is None or type == "audio":
        audio_dirs = [
            Path(settings.files_dir) / "audio" / "flac",
            Path(settings.files_dir) / "audio" / "wav",
        ]
        for audio_dir in audio_dirs:
            if audio_dir.exists():
                for audio_file in sorted(audio_dir.glob("*")):
                    if audio_file.suffix.lower() in [".flac", ".wav", ".mp3", ".ogg"]:
                        rel_path = f"audio/{audio_dir.name}/{audio_file.name}"
                        results.append(SongRecord(
                            id=audio_file.stem,
                            title=audio_file.stem,
                            url=f"/files/{rel_path}",
                            type="audio"
                        ))
    
    return results


@api_router.get("/images", response_model=List[ImageRecord])
async def list_images():
    """List all available images."""
    results = []
    images_dir = Path(settings.files_dir) / "image-effects" / "outputs"
    if images_dir.exists():
        for date_dir in sorted(images_dir.iterdir()):
            if date_dir.is_dir():
                for img_file in date_dir.glob("*"):
                    if img_file.suffix.lower() in [".jpg", ".jpeg", ".png", ".webp", ".gif"]:
                        results.append(ImageRecord(
                            url=f"/files/image-effects/outputs/{date_dir.name}/{img_file.name}",
                            description=f"Generated image from {date_dir.name}",
                            tags=["generated", date_dir.name]
                        ))
    return results


@api_router.get("/audio", response_model=List[SongRecord])
async def list_audio():
    """List all available audio files."""
    results = []
    audio_dirs = [
        Path(settings.files_dir) / "audio" / "flac",
        Path(settings.files_dir) / "audio" / "wav",
    ]
    for audio_dir in audio_dirs:
        if audio_dir.exists():
            for audio_file in sorted(audio_dir.glob("*")):
                if audio_file.suffix.lower() in [".flac", ".wav", ".mp3", ".ogg"]:
                    rel_path = f"audio/{audio_dir.name}/{audio_file.name}"
                    results.append(SongRecord(
                        id=audio_file.stem,
                        title=audio_file.stem,
                        url=f"/files/{rel_path}",
                        type="audio"
                    ))
    return results


@api_router.get("/videos", response_model=List[dict])
async def list_videos():
    """List all available video files."""
    results = []
    videos_dir = Path(settings.files_dir) / "videos"
    if videos_dir.exists():
        for video_file in sorted(videos_dir.glob("*")):
            if video_file.suffix.lower() in [".mp4", ".webm", ".mov", ".avi"]:
                results.append({
                    "id": video_file.stem,
                    "title": video_file.stem,
                    "url": f"/files/videos/{video_file.name}",
                    "type": "video"
                })
    return results


@api_router.get("/media")
async def list_media(
    type: Optional[str] = Query(None, description="Filter by type: audio, image, video, all")
):
    """List all available media files. More intuitive endpoint than /songs."""
    if type == "image":
        return {"images": await list_images(), "audio": [], "videos": []}
    elif type == "audio":
        return {"images": [], "audio": await list_audio(), "videos": []}
    elif type == "video":
        return {"images": [], "audio": [], "videos": await list_videos()}
    else:
        return {
            "images": await list_images(),
            "audio": await list_audio(),
            "videos": await list_videos()
        }


# ====================== Renderer Status ======================

@api_router.get("/renderer/status")
async def renderer_status():
    """Get renderer status and capabilities."""
    return {
        "backends": ["webgpu", "webgl2"],
        "default": "webgpu",
        "wasm_available": True,
        "wasm_module_url": "/wasm/pixelocity_wasm.js",
        "wasm_memory_required": 134217728  # 128MB
    }
