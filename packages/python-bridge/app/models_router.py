"""Model serving router with range header support for WebLLM."""

import logging
from pathlib import Path
from typing import Optional, Tuple

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from .config import settings

logger = logging.getLogger(__name__)
models_router = APIRouter(prefix="/models", tags=["models"])

# MIME types for model files
MODEL_MIME_TYPES = {
    ".wasm": "application/wasm",
    ".bin": "application/octet-stream",
    ".json": "application/json",
    ".mlmodel": "application/octet-stream",
    ".onnx": "application/octet-stream",
    ".safetensors": "application/octet-stream",
    ".ckpt": "application/octet-stream",
    ".pt": "application/octet-stream",
    ".pth": "application/octet-stream",
}


def _get_models_dir() -> Path:
    """Get the models storage directory."""
    base = Path(settings.files_dir)
    models_dir = base / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    return models_dir


def _parse_range_header(range_header: str, file_size: int) -> Tuple[int, int]:
    """
    Parse HTTP Range header.
    Returns (start, end) byte positions.
    
    Supports formats:
    - bytes=start-end
    - bytes=start- (from start to end of file)
    - bytes=-end (last 'end' bytes)
    """
    try:
        if not range_header.startswith("bytes="):
            raise ValueError("Invalid range unit")
        
        range_spec = range_header[6:]  # Remove "bytes="
        
        if "-" not in range_spec:
            raise ValueError("Invalid range format")
        
        start_str, end_str = range_spec.split("-", 1)
        
        if start_str == "" and end_str != "":
            # bytes=-500 (last 500 bytes)
            end = int(end_str)
            start = max(0, file_size - end)
            end = file_size - 1
        elif start_str != "" and end_str == "":
            # bytes=500- (from byte 500 to end)
            start = int(start_str)
            end = file_size - 1
        elif start_str != "" and end_str != "":
            # bytes=500-999
            start = int(start_str)
            end = min(int(end_str), file_size - 1)
        else:
            raise ValueError("Invalid range format")
        
        # Validate range
        if start < 0 or start >= file_size:
            raise ValueError("Range start out of bounds")
        if end < start:
            raise ValueError("Range end before start")
        
        return start, end
        
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid range header: {e}")


def _get_mime_type(file_path: Path) -> str:
    """Get MIME type for model file."""
    suffix = file_path.suffix.lower()
    return MODEL_MIME_TYPES.get(suffix, "application/octet-stream")


@models_router.get("/health")
async def models_health_check():
    """Health check for model serving."""
    models_dir = _get_models_dir()
    return {
        "status": "healthy",
        "models_dir": str(models_dir),
        "models_dir_exists": models_dir.exists(),
    }


@models_router.get("/list")
async def list_models():
    """List all available models in the storage."""
    models_dir = _get_models_dir()
    models = []
    
    for model_dir in sorted(models_dir.iterdir()):
        if not model_dir.is_dir():
            continue
        
        # Look for mlc-chat-config.json or similar
        config_file = model_dir / "mlc-chat-config.json"
        if not config_file.exists():
            config_file = model_dir / "config.json"
        
        model_info = {
            "id": model_dir.name,
            "path": f"/models/{model_dir.name}",
            "files": [],
            "has_config": config_file.exists(),
        }
        
        # List model files
        for file in model_dir.iterdir():
            if file.is_file():
                model_info["files"].append({
                    "name": file.name,
                    "size": file.stat().st_size,
                    "url": f"/models/{model_dir.name}/{file.name}",
                })
        
        models.append(model_info)
    
    return {"models": models, "total": len(models)}


@models_router.get("/{model_id}/{file_path:path}")
async def serve_model_file(
    request: Request,
    model_id: str,
    file_path: str,
    range_header: Optional[str] = Header(None, alias="range"),
):
    """
    Serve model files with full support for:
    - Range headers (for resume/partial downloads)
    - CORS headers (for cross-origin access)
    - Proper MIME types
    
    This enables WebLLM to efficiently load models with chunked downloads.
    """
    models_dir = _get_models_dir()
    model_dir = models_dir / model_id
    target_file = (model_dir / file_path).resolve()
    
    # Security: Prevent directory traversal
    if not str(target_file).startswith(str(model_dir.resolve())):
        logger.warning(f"Directory traversal attempt: {model_id}/{file_path}")
        raise HTTPException(status_code=403, detail="Access denied")
    
    if not target_file.exists() or not target_file.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    
    file_size = target_file.stat().st_size
    mime_type = _get_mime_type(target_file)
    
    # Handle range requests
    if range_header:
        try:
            start, end = _parse_range_header(range_header, file_size)
            content_length = end - start + 1
            
            logger.debug(f"Range request: {range_header} -> bytes {start}-{end}/{file_size}")
            
            def iter_file():
                with open(target_file, "rb") as f:
                    f.seek(start)
                    remaining = content_length
                    chunk_size = 64 * 1024  # 64KB chunks
                    while remaining > 0:
                        to_read = min(chunk_size, remaining)
                        chunk = f.read(to_read)
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        yield chunk
            
            headers = {
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(content_length),
                "Cache-Control": "public, max-age=86400",
            }
            
            return StreamingResponse(
                iter_file(),
                status_code=206,  # Partial Content
                media_type=mime_type,
                headers=headers,
            )
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error handling range request: {e}")
            raise HTTPException(status_code=416, detail="Range not satisfiable")
    
    # Full file request
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(file_size),
        "Cache-Control": "public, max-age=86400",
    }
    
    return StreamingResponse(
        file_sender(target_file),
        media_type=mime_type,
        headers=headers,
    )


def file_sender(file_path: Path):
    """Generator to stream file in chunks."""
    chunk_size = 256 * 1024  # 256KB chunks for efficiency
    with open(file_path, "rb") as f:
        while chunk := f.read(chunk_size):
            yield chunk


# =============================================================================
# TTS Model Endpoints (Supertonic)
# =============================================================================

def _get_tts_models_dir() -> Path:
    """Get the TTS models storage directory."""
    base = Path(settings.files_dir)
    tts_dir = base / "models" / "tts"
    tts_dir.mkdir(parents=True, exist_ok=True)
    return tts_dir


@models_router.get("/tts/list")
async def list_tts_models():
    """List all available TTS models (Supertonic format)."""
    tts_dir = _get_tts_models_dir()
    models = []
    
    # Look for model files
    expected_files = [
        "duration_predictor.onnx",
        "text_encoder.onnx",
        "vector_estimator.onnx",
        "vocoder.onnx",
        "tts.json",
        "unicode_indexer.json",
    ]
    
    # Check if main models exist
    main_models_exist = all((tts_dir / f).exists() for f in expected_files[:4])
    
    model_info = {
        "id": "supertonic",
        "name": "Supertonic TTS",
        "path": "/models/tts",
        "files": [],
        "voice_styles": [],
        "is_complete": main_models_exist,
    }
    
    # List model files
    if tts_dir.exists():
        for file in sorted(tts_dir.iterdir()):
            if file.is_file():
                model_info["files"].append({
                    "name": file.name,
                    "size": file.stat().st_size,
                    "url": f"/models/tts/{file.name}",
                })
        
        # List voice styles
        voice_styles_dir = tts_dir / "voice_styles"
        if voice_styles_dir.exists():
            for style_file in sorted(voice_styles_dir.glob("*.json")):
                model_info["voice_styles"].append({
                    "name": style_file.stem,
                    "file": style_file.name,
                    "url": f"/models/tts/voice_styles/{style_file.name}",
                })
    
    models.append(model_info)
    
    return {"models": models, "total": len(models)}


@models_router.get("/tts/health")
async def tts_health_check():
    """Check if TTS models are available."""
    tts_dir = _get_tts_models_dir()
    
    required_files = [
        "duration_predictor.onnx",
        "text_encoder.onnx",
        "vector_estimator.onnx",
        "vocoder.onnx",
    ]
    
    config_files = [
        "tts.json",
        "unicode_indexer.json",
    ]
    
    missing_models = [f for f in required_files if not (tts_dir / f).exists()]
    missing_configs = [f for f in config_files if not (tts_dir / f).exists()]
    
    # Check voice styles
    voice_styles_dir = tts_dir / "voice_styles"
    voice_styles = []
    if voice_styles_dir.exists():
        voice_styles = [f.stem for f in voice_styles_dir.glob("*.json")]
    
    return {
        "status": "healthy" if not missing_models else "incomplete",
        "tts_dir": str(tts_dir),
        "tts_dir_exists": tts_dir.exists(),
        "required_models": {
            "total": len(required_files),
            "available": len(required_files) - len(missing_models),
            "missing": missing_models,
        },
        "config_files": {
            "total": len(config_files),
            "available": len(config_files) - len(missing_configs),
            "missing": missing_configs,
        },
        "voice_styles": voice_styles,
        "is_ready": len(missing_models) == 0 and len(missing_configs) == 0,
    }


@models_router.head("/{model_id}/{file_path:path}")
async def head_model_file(
    request: Request,
    model_id: str,
    file_path: str,
):
    """
    Handle HEAD requests for model files.
    Returns headers without body - useful for checking file existence and size.
    """
    models_dir = _get_models_dir()
    model_dir = models_dir / model_id
    target_file = (model_dir / file_path).resolve()
    
    # Security: Prevent directory traversal
    if not str(target_file).startswith(str(model_dir.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")
    
    if not target_file.exists() or not target_file.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    
    file_size = target_file.stat().st_size
    mime_type = _get_mime_type(target_file)
    
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(file_size),
        "Content-Type": mime_type,
        "Cache-Control": "public, max-age=86400",
    }
    
    return Response(headers=headers, status_code=200)
