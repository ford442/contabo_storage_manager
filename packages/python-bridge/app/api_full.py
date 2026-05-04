# app.py - Storage Manager (Complete v3 with Shader Ratings)
# Structure score: 9/10 (fully featured)

import os
import json
import uuid
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional
from datetime import datetime
from enum import Enum
import io
from ftplib import FTP
import uvicorn
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form, Query
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, PlainTextResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from aiocache import Cache

# Google Cloud Imports
from google.cloud import storage
from google.oauth2 import service_account

# Include webhook and notes routers for cloud_notes / rain_edit compatibility
from .webhooks import webhook_router
from .notes_router import notes_router

# ========================= CONFIGURATION =========================
BUCKET_NAME = os.environ.get("GCP_BUCKET_NAME")
CREDENTIALS_JSON = os.environ.get("GCP_CREDENTIALS")
# --- FTP CONFIGURATION ---
FTP_HOST = os.environ.get("FTP_HOST", "")
FTP_USER = os.environ.get("FTP_USER", "")
FTP_PASS = os.environ.get("FTP_PASS", "")
FTP_DIR = os.environ.get("FTP_DIR", "/shaders")
FTP_ENABLED = bool(FTP_HOST)
# --- STORAGE MAP ---
# Defines the folder structure inside the bucket
STORAGE_MAP = {
    "song": {"folder": "songs/", "index": "songs/_songs.json"},
    "pattern": {"folder": "patterns/", "index": "patterns/_patterns.json"},
    "bank": {"folder": "banks/", "index": "banks/_banks.json"},
    "sample": {"folder": "samples/", "index": "samples/_samples.json"},
    "music": {"folder": "music/", "index": "music/_music.json"},
    "note": {"folder": "notes/", "index": "notes/_notes.json"},
    "shader": {"folder": "shaders/", "index": "shaders/_shaders.json"},
    "brainfuck": {
        "folder": "brainfuck/",
        "index": "brainfuck/_brainfuck.json"
    },
    "image": {"folder": "images/", "index": "images/_images.json"},
    "video": {"folder": "videos/", "index": "videos/_videos.json"},
    "default": {"folder": "misc/", "index": "misc/_misc.json"},
}

# --- GLOBAL OBJECTS ---
gcs_client = None
bucket = None
io_executor = ThreadPoolExecutor(max_workers=20)
cache = Cache(Cache.MEMORY)
INDEX_LOCK = asyncio.Lock()

# ------------------------ structured logging ------------------------
_log_handler = logging.StreamHandler()
_log_handler.setFormatter(logging.Formatter("%(message)s"))
_logger = logging.getLogger("storage_manager")
_logger.setLevel(logging.DEBUG)
if not _logger.handlers:
    _logger.addHandler(_log_handler)
    _logger.propagate = False


def _log_event(event: str, level: str = "info", **kwargs):
    payload = {"event": event, "ts": datetime.utcnow().isoformat() + "Z", **kwargs}
    message = json.dumps(payload, default=str)
    if level == "debug":
        _logger.debug(message)
    elif level == "warning":
        _logger.warning(message)
    else:
        _logger.info(message)


async def cache_get(key):
    value = await cache.get(key)
    if value is None:
        _log_event("cache_miss", level="debug", key=key)
    else:
        _log_event("cache_hit", level="debug", key=key)
    return value


async def cache_set(key, value, ttl=None):
    await cache.set(key, value, ttl=ttl)
    _log_event("cache_set", level="debug", key=key, ttl=ttl)


async def cache_delete(key):
    await cache.delete(key)
    _log_event("cache_delete", level="debug", key=key)


async def cache_clear():
    await cache.clear()
    _log_event("cache_clear", level="debug")


# ========================= HELPERS =========================
def get_gcs_client():
    if CREDENTIALS_JSON:
        cred_info = json.loads(CREDENTIALS_JSON)
        creds = service_account.Credentials.from_service_account_info(cred_info)
        return storage.Client(credentials=creds)
    return storage.Client()

async def run_io(func, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(io_executor, lambda: func(*args, **kwargs))
# --- FTP HELPERS ---
def _fetch_ftp_file_sync(filename: str) -> str:
    """Connect to FTP, download a .wgsl file, return as UTF-8 string."""
    ftp = FTP(FTP_HOST)
    try:
        ftp.login(FTP_USER, FTP_PASS)
        ftp.cwd(FTP_DIR)
        buffer = io.BytesIO()
        ftp.retrbinary(f"RETR {filename}", buffer.write)
        buffer.seek(0)
        return buffer.read().decode("utf-8")
    finally:
        try:
            ftp.quit()
        except Exception:
            ftp.close()

def _list_ftp_files_sync() -> list:
    """List all .wgsl files on the FTP server."""
    ftp = FTP(FTP_HOST)
    try:
        ftp.login(FTP_USER, FTP_PASS)
        ftp.cwd(FTP_DIR)
        files = ftp.nlst()
        return [f for f in files if f.endswith(".wgsl")]
    finally:
        try:
            ftp.quit()
        except Exception:
            ftp.close()
# --- LIFESPAN ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global gcs_client, bucket
    try:
        gcs_client = get_gcs_client()
        bucket = gcs_client.bucket(BUCKET_NAME)
        print(f"--- GCS CONNECTED: {BUCKET_NAME} ---")

        # Seed shaders if index is empty
        shader_config = STORAGE_MAP["shader"]
        try:
            shader_index = await run_io(_read_json_sync, shader_config["index"])
            if not isinstance(shader_index, list) or len(shader_index) == 0:
                seed_file = Path(__file__).parent / "seed_shaders.json"
                if seed_file.exists():
                    with open(seed_file) as f:
                        seed_data = json.load(f)
                    await run_io(_write_json_sync, shader_config["index"], seed_data)
                    print(f"--- SEEDED {len(seed_data)} SHADERS ---")
        except Exception as e:
            print(f"!!! SHADER SEEDING FAILED: {e}")
    except Exception as e:
        print(f"!!! GCS CONNECTION FAILED: {e}")
    yield
    io_executor.shutdown()

app = FastAPI(lifespan=lifespan)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    _log_event(
        "http_exception",
        level="warning",
        path=str(request.url),
        method=request.method,
        status_code=exc.status_code,
        detail=str(exc.detail)
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": str(exc.detail) if exc.detail else "HTTP error", "code": str(exc.status_code)}
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    _log_event(
        "validation_error",
        level="warning",
        path=str(request.url),
        method=request.method,
        errors=exc.errors()
    )
    return JSONResponse(
        status_code=422,
        content={"error": "Validation error", "code": "validation_error", "details": exc.errors()}
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    _log_event(
        "unhandled_exception",
        level="error",
        path=str(request.url),
        method=request.method,
        error=str(exc)
    )
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "code": "internal_error"}
    )

# ========================= CORS =========================
ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "https://test.1ink.us",
    "https://go.1ink.us",
    "https://noahcohn.com",
    "https://storage.noahcohn.com",
    "https://code.noahcohn.com",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers from the standard bridge so cloud_notes webhook + notes APIs work
app.include_router(webhook_router)
app.include_router(notes_router)

# ========================= MODELS =========================
class ItemPayload(BaseModel):
    name: str
    author: str
    description: Optional[str] = ""
    type: str = "song"
    data: dict
    rating: Optional[int] = None

class MetaData(BaseModel):
    id: str
    name: str
    author: str
    date: str
    type: str
    description: Optional[str] = ""
    filename: str
    rating: Optional[int] = None
    genre: Optional[str] = None
    last_played: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    coordinate: Optional[int] = None
    stars: Optional[float] = None
    rating_count: Optional[int] = None
    play_count: Optional[int] = None
    updated_at: Optional[str] = None
    thumbnail: Optional[str] = None
    thumbnail_url: Optional[str] = None

class ShaderMeta(MetaData):
    thumbnail: Optional[str] = None
    thumbnail_url: Optional[str] = None
    category: Optional["ShaderCategory"] = None
    params: Optional[List["ShaderParam"]] = None

class ShaderUploadPayload(BaseModel):
    name: str
    author: str
    description: Optional[str] = ""
    tags: Optional[List[str]] = Field(default_factory=list)
    category: Optional["ShaderCategory"] = None
    coordinate: Optional[int] = None
    params: Optional[List["ShaderParam"]] = None

class ShaderListResponse(BaseModel):
    shaders: List["ShaderMeta"]

# --- GCS I/O HELPERS ---
def _read_json_sync(blob_path):
    blob = bucket.blob(blob_path)
    if blob.exists():
        return json.loads(blob.download_as_text())
    return []
def _write_json_sync(blob_path, data):
    blob = bucket.blob(blob_path)
    # Upload as JSON string with correct content type
    blob.upload_from_string(
        json.dumps(data),
        content_type='application/json'
    )

class AssetService:
    """Abstraction layer for asset index operations and cache management.

    The service centralizes reading and writing GCS-backed JSON indexes,
    applying an asyncio lock on index writes and invalidating cached results
    for affected asset categories.
    """

    def __init__(self, storage_map=None, index_lock=None, cache_obj=None):
        self.storage_map = storage_map or STORAGE_MAP
        self.index_lock = index_lock or INDEX_LOCK
        self.cache = cache_obj or cache

    def get_index_config(self, type: str):
        """Return the storage configuration for a given asset type.

        Falls back to the default configuration when the requested type
        is not explicitly mapped.
        """
        return self.storage_map.get(type, self.storage_map["default"])

    async def read_index(self, type: str) -> list:
        """Read and return the JSON index for the requested asset type.

        Returns an empty list if the index file is missing or malformed.
        """
        config = self.get_index_config(type)
        result = await run_io(_read_json_sync, config["index"])
        return result if isinstance(result, list) else []

    async def write_index(self, type: str, data: list):
        """Write a complete index payload and clear related cached entries."""
        config = self.get_index_config(type)
        async with self.index_lock:
            await run_io(_write_json_sync, config["index"], data)
            await self.clear_cache(type)
        _log_event("index_write", type=type, index=config["index"], count=len(data))

    def create_meta(self, payload, item_type: str, item_id: str) -> dict:
        """Build a normalized metadata dictionary for an indexed asset.

        Accepts either a dict or a Pydantic model and returns an index-ready
        JSON-compatible metadata dictionary.
        """
        values = {}
        if hasattr(payload, "dict") and callable(payload.dict):
            values = payload.dict()
        elif isinstance(payload, dict):
            values = payload
        else:
            values = {
                key: getattr(payload, key)
                for key in dir(payload)
                if not key.startswith("_") and hasattr(payload, key)
            }

        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        return {
            "id": item_id,
            "type": item_type,
            "name": values.get("name") or "",
            "author": values.get("author") or "Unknown",
            "description": values.get("description") or "",
            "date": values.get("date") or now,
            "filename": values.get("filename") or values.get("name") or "",
            "rating": values.get("rating"),
            "genre": values.get("genre"),
            "last_played": values.get("last_played"),
            "tags": values.get("tags") or [],
            "coordinate": values.get("coordinate"),
            "stars": values.get("stars"),
            "rating_count": values.get("rating_count"),
            "play_count": values.get("play_count"),
            "updated_at": values.get("updated_at"),
            "thumbnail": values.get("thumbnail"),
            "thumbnail_url": values.get("thumbnail_url"),
            "category": values.get("category"),
        }

    async def clear_cache(self, type: str = None):
        """Clear cached entries for a single type or flush the entire cache."""
        if type is None:
            await self.cache.clear()
            return

        type_terms = [f"{type}:", f":{type}", f"{type}s", f"{type}_", f"{type}-"]
        if hasattr(self.cache, "keys"):
            try:
                keys = await self.cache.keys("*")
                for key in keys:
                    if any(term in key for term in type_terms) or key == "library:all":
                        await self.cache.delete(key)
                return
            except Exception:
                pass

        await self.cache.clear()

asset_service = AssetService()
# --- ENDPOINTS ---
@app.get("/")
def home():
    return {"status": "online", "provider": "Google Cloud Storage"}
# --- 0.5 HEALTH CHECK & TEST DATA ---
@app.post("/api/admin/sync-music")
async def sync_music_folder(type: Optional[str] = Query(None, alias="type")):
    """Scans the music/ folder and rebuilds the music index.

    Optional ?type=music only allows targeting the music type explicitly.
    """
    if type is not None and type != "music":
        raise HTTPException(400, "sync-music only supports type=music")

    config = STORAGE_MAP["music"]
    report = {"added": 0, "removed": 0}

    try:
        blobs = await run_io(lambda: list(bucket.list_blobs(prefix=config["folder"])))

        audio_files = []
        for b in blobs:
            fname = b.name.replace(config["folder"], "")
            if fname and not b.name.endswith(config["index"]):
                lower = fname.lower()
                if lower.endswith((".flac", ".wav", ".mp3", ".ogg")):
                    audio_files.append({
                        "filename": fname,
                        "name": fname,
                        "size": b.size,
                        "url": b.public_url
                    })

        index_data = await asset_service.read_index("music")
        if not isinstance(index_data, list):
            index_data = []

        index_map = {item["filename"]: item for item in index_data}
        disk_set = set(f["filename"] for f in audio_files)

        new_index = [item for item in index_data if item["filename"] in disk_set]
        report["removed"] = len(index_data) - len(new_index)

        for file_info in audio_files:
            if file_info["filename"] not in index_map:
                new_entry = {
                    "id": str(uuid.uuid4()),
                    "filename": file_info["filename"],
                    "name": file_info["name"],
                    "type": "music",
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "author": "Unknown",
                    "description": "",
                    "rating": None,
                    "genre": None,
                    "last_played": None,
                    "url": file_info["url"],
                    "size": file_info["size"],
                    "updated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
                }
                new_index.insert(0, new_entry)
                report["added"] += 1

        for item in new_index:
            if "updated_at" not in item:
                item["updated_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

        if report["added"] > 0 or report["removed"] > 0:
            await asset_service.write_index("music", new_index)

        await cache_delete("library:music")
        await cache_delete("library:all")

        report["total"] = len(new_index)
        _log_event("sync_music_folder", type=type, report=report)
        return report
    except Exception as e:
        raise HTTPException(500, f"Failed to sync music: {str(e)}")
@app.post("/api/admin/seed-test-samples")
async def seed_test_samples():
    """Creates test sample entries for development."""
    config = STORAGE_MAP["sample"]
    test_samples = [
        {
            "id": "test-flac-001",
            "name": "Test Ambient Track.flac",
            "filename": "test-flac-001.flac",
            "type": "sample",
            "author": "Test Artist",
            "date": "2024-02-09",
            "description": "Test ambient track",
            "rating": 8,
            "genre": "ambient"
        },
        {
            "id": "test-wav-002",
            "name": "Test Bass Line.wav",
            "filename": "test-wav-002.wav",
            "type": "sample",
            "author": "Test Artist",
            "date": "2024-02-09",
            "description": "Test bass line",
            "rating": 7,
            "genre": "bass"
        },
        {
            "id": "test-flac-003",
            "name": "Unrated Demo.flac",
            "filename": "test-flac-003.flac",
            "type": "sample",
            "author": "Unknown",
            "date": "2024-02-09",
            "description": "Demo without rating",
            "rating": None,
            "genre": None
        }
    ]

    async with INDEX_LOCK:
        try:
            # Read existing
            index_data = await run_io(_read_json_sync, config["index"])
            if not isinstance(index_data, list):
                index_data = []

            # Add test samples (avoid duplicates)
            existing_ids = {item.get("id") for item in index_data}
            added = 0
            for sample in test_samples:
                if sample["id"] not in existing_ids:
                    index_data.insert(0, sample)
                    added += 1

            await run_io(_write_json_sync, config["index"], index_data)
            await cache_delete("library:sample")
            await cache_delete("library:all")

            return {"success": True, "added": added, "total": len(index_data)}
        except Exception as e:
            raise HTTPException(500, f"Failed to seed: {str(e)}")
@app.get("/api/health")
async def health_check():
    """Returns storage manager status and index counts."""
    status = {}
    for item_type, config in STORAGE_MAP.items():
        if item_type == "default":
            continue
        try:
            items = await run_io(_read_json_sync, config["index"])
            count = len(items) if isinstance(items, list) else 0
            status[item_type] = {"count": count, "status": "ok"}
        except Exception as e:
            status[item_type] = {"count": 0, "status": "error", "error": str(e)}
    return {"status": "online", "storage": status}
# --- 1. LISTING (Cached) ---
class SortBy(str, Enum):
    date = "date"
    updated_at = "updated_at"
    rating = "rating"
    name = "name"
    last_played = "last_played"
    genre = "genre"
    coordinate = "coordinate"  # NEW

class ShaderCategory(str, Enum):
    generative = "generative"
    reactive = "reactive"
    transition = "transition"
    filter = "filter"
    distortion = "distortion"

class SampleMetaUpdatePayload(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    rating: Optional[int] = Field(None, ge=1, le=10)
    genre: Optional[str] = None
    last_played: Optional[str] = None

class ShaderParam(BaseModel):
    name: str
    label: Optional[str] = None
    default: float = 0.5
    min: float = 0.0
    max: float = 1.0
    step: Optional[float] = 0.01
    description: Optional[str] = ""

class MetaPatch(BaseModel):
    name: Optional[str] = None
    rating: Optional[int] = Field(None, ge=0, le=10)
    genre: Optional[str] = None
    tags: Optional[List[str]] = None
    last_played: Optional[str] = None
    coordinate: Optional[int] = None  # NEW
    params: Optional[List[ShaderParam]] = None  # Shader parameter definitions
    updated_at: Optional[str] = None

class CoordinateSyncPayload(BaseModel):
    coordinates: dict
    overwrite: bool = False

ShaderMeta.update_forward_refs()
ShaderUploadPayload.update_forward_refs()
ShaderListResponse.update_forward_refs()

# ========================= GCS I/O HELPERS =========================
def _read_json_sync(blob_path):
    blob = bucket.blob(blob_path)
    if blob.exists():
        return json.loads(blob.download_as_text())
    return []

def _write_json_sync(blob_path, data):
    blob = bucket.blob(blob_path)
    blob.upload_from_string(json.dumps(data), content_type='application/json')

# ========================= ENDPOINTS =========================
@app.get("/")
def home():
    return {
        "status": "online",
        "provider": "Google Cloud Storage",
        "benchmark_ready": True,
        "features": ["shader_ratings", "play_tracking", "coordinate_system"],
        "endpoints": {
            "ratings_ui": "/ratings",
            "shaders": "/api/shaders",
            "shader_rate": "/api/shaders/{id}/rate",
            "shader_play": "/api/shaders/{id}/play",
            "sync_coords": "/api/admin/sync-coordinates"
        }
    }

@app.get("/api/health")
async def health_check():
    status_report = {}
    for item_type, config in STORAGE_MAP.items():
        try:
            index_data = await run_io(_read_json_sync, config["index"])
            status_report[item_type] = {
                "count": len(index_data) if isinstance(index_data, list) else 0,
                "status": "connected"
            }
        except Exception as e:
            status_report[item_type] = {"count": 0, "status": "error", "error": str(e)}
    return {
        "status": "online",
        "gcs_connected": bucket is not None,
        "storage": status_report
    }

# ========================= SHADER RATINGS UI =========================
@app.get("/ratings", response_class=HTMLResponse)
async def ratings_ui():
    """Serves the interactive star rating interface."""
    return """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Shader Ratings</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: linear-gradient(135deg, #0a0a0f 0%, #1a1a2e 100%);
      min-height: 100vh;
      color: #e0e0e0;
      padding: 20px;
    }
    .container { max-width: 1200px; margin: 0 auto; }
    h1 {
      text-align: center;
      margin-bottom: 30px;
      font-size: 28px;
      background: linear-gradient(90deg, #4a9eff, #a855f7);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }
    .filters {
      display: flex;
      gap: 15px;
      margin-bottom: 25px;
      flex-wrap: wrap;
      justify-content: center;
    }
    .filters select, .filters input {
      padding: 10px 15px;
      background: rgba(255,255,255,0.05);
      border: 1px solid rgba(255,255,255,0.1);
      border-radius: 8px;
      color: #fff;
      font-size: 14px;
      outline: none;
    }
    .filters select option { background: #1a1a2e; }
    .shader-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
      gap: 20px;
    }
    .shader-card {
      background: rgba(255,255,255,0.03);
      border: 1px solid rgba(255,255,255,0.08);
      border-radius: 12px;
      padding: 20px;
      transition: all 0.3s ease;
    }
    .shader-card:hover {
      background: rgba(255,255,255,0.06);
      transform: translateY(-2px);
      box-shadow: 0 8px 32px rgba(0,0,0,0.3);
    }
    .shader-header {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      margin-bottom: 12px;
    }
    .shader-name {
      font-size: 16px;
      font-weight: 600;
      color: #fff;
      line-height: 1.3;
    }
    .shader-id {
      font-size: 11px;
      color: #666;
      font-family: monospace;
      margin-top: 4px;
    }
    .category-badge {
      padding: 4px 10px;
      background: rgba(74, 158, 255, 0.15);
      border: 1px solid rgba(74, 158, 255, 0.3);
      border-radius: 20px;
      font-size: 11px;
      color: #4a9eff;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    .stars-container {
      display: flex;
      gap: 4px;
      margin: 15px 0;
    }
    .star {
      cursor: pointer;
      transition: all 0.2s;
      width: 28px;
      height: 28px;
    }
    .star:hover { transform: scale(1.15); }
    .star.filled path { fill: #ffd700; stroke: #ffd700; }
    .star.empty path { fill: transparent; stroke: #555; }
    .rating-info {
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 13px;
      color: #888;
    }
    .avg-rating {
      font-size: 18px;
      font-weight: 700;
      color: #ffd700;
    }
    .vote-count { color: #666; }
    .toast {
      position: fixed;
      bottom: 30px;
      left: 50%;
      transform: translateX(-50%) translateY(100px);
      background: rgba(30, 130, 76, 0.95);
      color: white;
      padding: 12px 24px;
      border-radius: 8px;
      font-size: 14px;
      opacity: 0;
      transition: all 0.3s ease;
      z-index: 1000;
    }
    .toast.show {
      opacity: 1;
      transform: translateX(-50%) translateY(0);
    }
    .loading {
      text-align: center;
      padding: 60px;
      color: #666;
    }
    .spinner {
      display: inline-block;
      width: 40px;
      height: 40px;
      border: 3px solid rgba(255,255,255,0.1);
      border-top-color: #4a9eff;
      border-radius: 50%;
      animation: spin 1s linear infinite;
      margin-bottom: 15px;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    .sort-buttons {
      display: flex;
      gap: 8px;
      justify-content: center;
      margin-bottom: 20px;
    }
    .sort-btn {
      padding: 8px 16px;
      background: rgba(255,255,255,0.05);
      border: 1px solid rgba(255,255,255,0.1);
      border-radius: 20px;
      color: #888;
      font-size: 13px;
      cursor: pointer;
      transition: all 0.2s;
    }
    .sort-btn:hover, .sort-btn.active {
      background: rgba(74, 158, 255, 0.2);
      border-color: rgba(74, 158, 255, 0.5);
      color: #4a9eff;
    }
    .coordinate-badge {
      display: inline-block;
      padding: 2px 6px;
      background: rgba(168, 85, 247, 0.15);
      border: 1px solid rgba(168, 85, 247, 0.3);
      border-radius: 4px;
      font-family: monospace;
      font-size: 11px;
      color: #a855f7;
      margin-left: 8px;
    }
    .play-count {
      font-size: 11px;
      color: #4a9eff;
      margin-left: 10px;
    }
  </style>
</head>
<body>
  <div class="container">
    <h1>⭐ Shader Ratings</h1>
    
    <div class="sort-buttons">
      <button class="sort-btn active" onclick="sortBy('rating')">⭐ By Rating</button>
      <button class="sort-btn" onclick="sortBy('date')">📅 By Date</button>
      <button class="sort-btn" onclick="sortBy('name')">🔤 By Name</button>
      <button class="sort-btn" onclick="sortBy('coordinate')">🔢 By Coordinate</button>
    </div>

    <div class="filters">
      <select id="categoryFilter" onchange="applyFilters()">
        <option value="">All Categories</option>
        <option value="generative">Generative</option>
        <option value="reactive">Reactive</option>
        <option value="transition">Transition</option>
        <option value="filter">Filter</option>
        <option value="distortion">Distortion</option>
      </select>
      <select id="minRating" onchange="applyFilters()">
        <option value="0">Any Rating</option>
        <option value="4.5">⭐⭐⭐⭐⭐ (4.5+)</option>
        <option value="4">⭐⭐⭐⭐ (4+)</option>
        <option value="3">⭐⭐⭐ (3+)</option>
      </select>
      <input type="text" id="search" placeholder="Search shaders..." onkeyup="applyFilters()">
    </div>

    <div id="shaderList" class="shader-grid">
      <div class="loading">
        <div class="spinner"></div>
        <div>Loading shaders...</div>
      </div>
    </div>
  </div>

  <div id="toast" class="toast"></div>

  <script>
    const API_URL = '';
    let shaders = [];
    let currentSort = 'rating';

    function getStarSvg(filled) {
      return `<svg class="star ${filled ? 'filled' : 'empty'}" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
        <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z" 
              fill="none" stroke-width="1.5" stroke-linejoin="round"/>
      </svg>`;
    }

    function renderStars(rating, count, shaderId) {
      const fullStars = Math.floor(rating);
      let html = '<div class="stars-container">';
      
      for (let i = 1; i <= 5; i++) {
        html += `<div onclick="rateShader('${shaderId}', ${i})" title="Rate ${i} star${i>1?'s':''}">${getStarSvg(i <= fullStars)}</div>`;
      }
      
      html += '</div>';
      html += `
        <div class="rating-info">
          <span class="avg-rating">${rating > 0 ? rating.toFixed(1) : '—'}</span>
          <span class="vote-count">${count} vote${count !== 1 ? 's' : ''}</span>
        </div>
      `;
      return html;
    }

    function showToast(message) {
      const toast = document.getElementById('toast');
      toast.textContent = message;
      toast.classList.add('show');
      setTimeout(() => toast.classList.remove('show'), 2500);
    }

    async function rateShader(shaderId, stars) {
      try {
        const formData = new FormData();
        formData.append('stars', stars);
        
        const response = await fetch(`${API_URL}/api/shaders/${shaderId}/rate`, {
          method: 'POST',
          body: formData
        });
        
        if (!response.ok) throw new Error('Failed to rate');
        
        const data = await response.json();
        showToast(`⭐ Rated ${stars} stars! New average: ${data.stars.toFixed(1)}`);
        
        const shader = shaders.find(s => s.id === shaderId);
        if (shader) {
          shader.stars = data.stars;
          shader.rating_count = data.rating_count;
          renderShaders();
        }
      } catch (err) {
        showToast('❌ Failed to submit rating');
        console.error(err);
      }
    }

    function sortBy(field) {
      currentSort = field;
      document.querySelectorAll('.sort-btn').forEach(btn => btn.classList.remove('active'));
      event.target.classList.add('active');
      
      const sortFns = {
        rating: (a, b) => b.stars - a.stars,
        date: (a, b) => new Date(b.date || 0) - new Date(a.date || 0),
        name: (a, b) => (a.name || '').localeCompare(b.name || ''),
        coordinate: (a, b) => (a.coordinate || 0) - (b.coordinate || 0)
      };
      
      shaders.sort(sortFns[field]);
      applyFilters();
    }

    function applyFilters() {
      const category = document.getElementById('categoryFilter').value;
      const minRating = parseFloat(document.getElementById('minRating').value);
      const search = document.getElementById('search').value.toLowerCase();
      
      let filtered = shaders.filter(s => {
        if (category && !s.tags?.includes(category) && s.type !== category) return false;
        if (minRating > 0 && (s.stars || 0) < minRating) return false;
        if (search && !s.name?.toLowerCase().includes(search) && !s.id.toLowerCase().includes(search)) return false;
        return true;
      });
      
      renderShaders(filtered);
    }

    function renderShaders(shaderList = shaders) {
      const container = document.getElementById('shaderList');
      
      if (shaderList.length === 0) {
        container.innerHTML = '<div class="loading">No shaders found</div>';
        return;
      }
      
      container.innerHTML = shaderList.map(shader => `
        <div class="shader-card">
          <div class="shader-header">
            <div>
              <div class="shader-name">
                ${shader.name || shader.id}
                ${shader.coordinate ? `<span class="coordinate-badge">#${shader.coordinate}</span>` : ''}
                ${shader.play_count ? `<span class="play-count">▶ ${shader.play_count}</span>` : ''}
              </div>
              <div class="shader-id">${shader.id}</div>
            </div>
            <span class="category-badge">${shader.type || 'shader'}</span>
          </div>
          ${renderStars(shader.stars || 0, shader.rating_count || 0, shader.id)}
        </div>
      `).join('');
    }

    async function loadShaders() {
      try {
        const response = await fetch(`${API_URL}/api/shaders?sort_by=${currentSort}`);
        if (!response.ok) throw new Error('Failed to load');
        
        shaders = await response.json();
        renderShaders();
      } catch (err) {
        document.getElementById('shaderList').innerHTML = `
          <div class="loading">
            <div style="color: #c0392b; margin-bottom: 10px;">Failed to load shaders</div>
            <div style="font-size: 13px;">${err.message}</div>
          </div>
        `;
        console.error(err);
      }
    }

    loadShaders();
  </script>
</body>
</html>
"""

# ========================= SHADER ENDPOINTS =========================

@app.get("/api/shaders", response_model=List[ShaderMeta])
async def list_shaders(
    category: Optional[ShaderCategory] = Query(None),
    min_stars: float = Query(0.0, ge=0, le=5),
    sort_by: SortBy = Query(SortBy.rating)
):
    cache_key = f"shaders:list:{category}:{min_stars}:{sort_by}"
    cached = await cache_get(cache_key)
    if cached:
        return cached

    try:
        index = await asset_service.read_index("shader")

        if category:
            index = [
                s for s in index
                if category.value in s.get("tags", [])
                or category.value.lower() in s.get("description", "").lower()
            ]
        if min_stars > 0:
            index = [s for s in index if s.get("stars", 0) >= min_stars]

        reverse = sort_by in [SortBy.rating, SortBy.date, SortBy.last_played]
        if sort_by is SortBy.rating:
            index.sort(key=lambda s: s.get("stars", 0), reverse=reverse)
        elif sort_by is SortBy.date:
            index.sort(key=lambda s: s.get("date", ""), reverse=reverse)
        elif sort_by is SortBy.name:
            index.sort(key=lambda s: s.get("name", "").lower())
        elif sort_by is SortBy.coordinate:
            index.sort(key=lambda s: s.get("coordinate", 9999))

        for shader in index:
            shader.setdefault("stars", 0.0)
            shader.setdefault("rating_count", 0)
            shader.setdefault("play_count", 0)
            if shader.get("thumbnail"):
                thumbnail_path = f"{STORAGE_MAP['shader']['folder']}{shader['thumbnail']}"
                shader["thumbnail_url"] = bucket.blob(thumbnail_path).public_url

        await cache_set(cache_key, index, ttl=300)
        return index
    except Exception as e:
        raise HTTPException(500, f"Failed to list shaders: {str(e)}")

@app.get("/api/shaders/{shader_id}", response_model=ShaderMeta)
async def get_shader_meta(shader_id: str):
    """Get shader metadata including stars, rating_count, play_count, coordinate."""
    index = await asset_service.read_index("shader")
    entry = next((s for s in index if s.get("id") == shader_id), None)
    if not entry:
        raise HTTPException(404, "Shader not found")

    entry.setdefault("stars", 0.0)
    entry.setdefault("rating_count", 0)
    entry.setdefault("play_count", 0)
    entry.setdefault("coordinate", None)
    if entry.get("thumbnail"):
        thumbnail_path = f"{STORAGE_MAP['shader']['folder']}{entry['thumbnail']}"
        entry["thumbnail_url"] = bucket.blob(thumbnail_path).public_url
    return entry

@app.post("/api/shaders/{shader_id}/rate")
async def rate_shader(shader_id: str, stars: float = Form(...)):
    """Rate a shader 1-5 stars. Updates average and count."""
    if not 1 <= stars <= 5:
        raise HTTPException(400, "Stars must be between 1 and 5")

    try:
        index = await asset_service.read_index("shader")
        entry = next((s for s in index if s.get("id") == shader_id), None)
        if not entry:
            raise HTTPException(404, "Shader not found")

        current_stars = entry.get("stars", 0.0)
        current_count = entry.get("rating_count", 0)
        new_count = current_count + 1
        new_stars = ((current_stars * current_count) + stars) / new_count

        entry["stars"] = round(new_stars, 2)
        entry["rating_count"] = new_count

        await asset_service.write_index("shader", index)
        _log_event("rate_shader", shader_id=shader_id, stars=stars, rating_count=entry["rating_count"])
        return {
            "id": shader_id,
            "stars": entry["stars"],
            "rating_count": entry["rating_count"],
            "your_rating": stars
        }
    except HTTPException:
        raise
    except Exception as e:
        _log_event("rate_shader_failed", level="error", shader_id=shader_id, error=str(e))
        raise HTTPException(500, f"Rating failed: {str(e)}")

@app.post("/api/shaders/{shader_id}/play")
async def record_shader_play(shader_id: str):
    """Record that a shader was played. Increments play_count."""
    now = datetime.now().isoformat()

    try:
        index = await asset_service.read_index("shader")
        entry = next((s for s in index if s.get("id") == shader_id), None)
        if not entry:
            raise HTTPException(404, "Shader not found")

        entry["play_count"] = (entry.get("play_count") or 0) + 1
        entry["last_played"] = now

        await asset_service.write_index("shader", index)
        _log_event("record_shader_play", shader_id=shader_id, play_count=entry["play_count"])
        return {
            "success": True,
            "id": shader_id,
            "play_count": entry["play_count"],
            "last_played": now
        }
    except HTTPException:
        raise
    except Exception as e:
        _log_event("record_shader_play_failed", level="error", shader_id=shader_id, error=str(e))
        raise HTTPException(500, f"Failed to record play: {str(e)}")

@app.post("/api/shaders/upload")
async def upload_shader(
    file: UploadFile = File(...),
    thumbnail: UploadFile = File(None),
    name: str = Form(...),
    description: str = Form(""),
    tags: str = Form(""),
    author: str = Form("ford442"),
    coordinate: Optional[int] = Form(None)
):
    """Upload a .wgsl shader file with metadata."""
    if not file.filename.endswith(".wgsl"):
        raise HTTPException(400, "Only .wgsl files allowed")
    
    shader_id = str(uuid.uuid4())
    storage_filename = f"{shader_id}.wgsl"
    config = STORAGE_MAP["shader"]
    full_path = f"{config['folder']}{storage_filename}"
    
    meta_payload = {
        "name": name,
        "author": author,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "type": "shader",
        "description": description,
        "tags": [t.strip() for t in tags.split(",")] if tags else [],
        "filename": storage_filename,
        "coordinate": coordinate,
        "stars": 0.0,
        "rating_count": 0,
        "play_count": 0,
        "updated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "thumbnail": None
    }
    meta = asset_service.create_meta(meta_payload, "shader", shader_id)

    try:
        blob = bucket.blob(full_path)
        await run_io(blob.upload_from_file, file.file, content_type="text/plain")

        if thumbnail is not None:
            if not thumbnail.filename.lower().endswith(".png"):
                raise HTTPException(400, "Only .png thumbnails are supported")
            thumb_path = f"{config['folder']}{shader_id}.png"
            thumb_blob = bucket.blob(thumb_path)
            await run_io(thumb_blob.upload_from_file, thumbnail.file, content_type="image/png")
            meta["thumbnail"] = f"{shader_id}.png"
            meta["thumbnail_url"] = thumb_blob.public_url

        index = await asset_service.read_index("shader")
        index.insert(0, meta)
        await asset_service.write_index("shader", index)
        _log_event("upload_shader", shader_id=shader_id, filename=storage_filename)
        return {"success": True, "id": shader_id, "meta": meta}
    except Exception as e:
        raise HTTPException(500, f"Upload failed: {str(e)}")

@app.get("/api/shaders/{shader_id}/code")
async def get_shader_code(shader_id: str):
    """Returns the actual .wgsl shader code."""
    entry = next((s for s in await asset_service.read_index("shader") if s.get("id") == shader_id), None)
    if not entry:
        raise HTTPException(404, "Shader not found")

    blob_path = f"{STORAGE_MAP['shader']['folder']}{entry['filename']}"
    blob = bucket.blob(blob_path)
    if not await run_io(blob.exists):
        raise HTTPException(404, "Shader file not found")

    code = await run_io(blob.download_as_text)
    return {"id": shader_id, "code": code, "name": entry.get("name")}

@app.get("/api/shaders/{shader_id}/thumbnail")
async def get_shader_thumbnail(shader_id: str):
    """Returns the shader thumbnail image if present."""
    entry = next((s for s in await asset_service.read_index("shader") if s.get("id") == shader_id), None)
    if not entry or not entry.get("thumbnail"):
        raise HTTPException(404, "Thumbnail not found")

    thumb_path = f"{STORAGE_MAP['shader']['folder']}{entry['thumbnail']}"
    blob = bucket.blob(thumb_path)
    if not await run_io(blob.exists):
        raise HTTPException(404, "Thumbnail not found")

    def iterfile():
        with blob.open("rb") as f:
            while chunk := f.read(1024 * 1024):
                yield chunk

    return StreamingResponse(iterfile(), media_type="image/png")

@app.put("/api/shaders/{shader_id}")
@app.post("/api/shaders/{shader_id}/update")
async def update_shader_metadata(shader_id: str, payload: MetaPatch):
    """Update shader metadata (name, rating, coordinate, etc)."""
    try:
        index = await asset_service.read_index("shader")
        entry_idx = next((i for i, s in enumerate(index) if s.get("id") == shader_id), -1)
        if entry_idx == -1:
            raise HTTPException(404, "Shader not found")

        entry = index[entry_idx]
        updated = {}
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

        if payload.name is not None:
            entry["name"] = payload.name
            updated["name"] = payload.name
        if payload.rating is not None:
            entry["rating"] = payload.rating
            updated["rating"] = payload.rating
        if payload.coordinate is not None:
            entry["coordinate"] = payload.coordinate
            updated["coordinate"] = payload.coordinate
        if payload.tags is not None:
            entry["tags"] = payload.tags
            updated["tags"] = payload.tags
        if payload.params is not None:
            entry["params"] = [p.dict() for p in payload.params]
            updated["params"] = f"{len(payload.params)} parameters"
        if payload.updated_at is not None:
            entry["updated_at"] = payload.updated_at
            updated["updated_at"] = payload.updated_at
        elif updated:
            entry["updated_at"] = now
            updated["updated_at"] = entry["updated_at"]

        if updated:
            await asset_service.write_index("shader", index)
            _log_event("update_shader_metadata", shader_id=shader_id, updated=updated)

        return {"success": True, "id": shader_id, "updated": updated}
    except HTTPException:
        raise
    except Exception as e:
        _log_event("update_shader_metadata_failed", level="error", shader_id=shader_id, error=str(e))
        raise HTTPException(500, f"Update failed: {str(e)}")

# ========================= COORDINATE SYNC =========================

@app.post("/api/admin/sync-coordinates")
async def sync_shader_coordinates(payload: CoordinateSyncPayload):
    """Sync coordinates from shader_coordinates.json."""
    try:
        index = await asset_service.read_index("shader")
        updated = 0
        skipped = 0

        for entry in index:
            shader_id = entry.get("id")
            if shader_id in payload.coordinates:
                existing_coord = entry.get("coordinate")
                new_coord = payload.coordinates[shader_id]
                if existing_coord is None or payload.overwrite:
                    entry["coordinate"] = new_coord
                    updated += 1
                else:
                    skipped += 1

        if updated > 0:
            await asset_service.write_index("shader", index)
            _log_event("sync_shader_coordinates", updated=updated, skipped=skipped)

        return {
            "success": True,
            "updated": updated,
            "skipped": skipped,
            "total": len(index)
        }
    except Exception as e:
        _log_event("sync_shader_coordinates_failed", level="error", error=str(e))
        raise HTTPException(500, f"Sync failed: {str(e)}")

# ========================= SONGS / SAMPLES / MUSIC (Original) =========================

@app.get("/api/songs", response_model=List[MetaData])
async def list_library(
    type: Optional[str] = Query(None),
    genre: Optional[str] = Query(None),
    min_rating: Optional[int] = Query(None, ge=1, le=10),
    sort_by: SortBy = Query(SortBy.date),
    sort_desc: bool = Query(True)
):
    cache_key = f"library:{type or 'all'}:{sort_by}:{sort_desc}:{genre}:{min_rating}"
    cached = await cache_get(cache_key)
    if cached:
        return cached

    search_types = [type] if type else ["song", "pattern", "bank", "sample", "music", "shader", "image", "video"]
    results = []

    def effective_rating(item):
        return item.get("rating") if item.get("rating") is not None else item.get("stars", 0)

    for t in search_types:
        try:
            items = await asset_service.read_index(t)
            if isinstance(items, list):
                if t == "shader":
                    for shader in items:
                        shader.setdefault("stars", 0.0)
                        shader.setdefault("rating_count", 0)
                        shader.setdefault("play_count", 0)
                        shader.setdefault("rating", shader.get("stars", 0.0))
                        if shader.get("thumbnail"):
                            shader["thumbnail_url"] = bucket.blob(
                                f"{STORAGE_MAP['shader']['folder']}{shader['thumbnail']}"
                            ).public_url
                results.extend(items)
        except Exception as e:
            _log_event("read_index_failed", level="error", type=t, error=str(e))

    if genre:
        results = [r for r in results if r.get("genre") == genre]
    if min_rating is not None:
        results = [r for r in results if effective_rating(r) >= min_rating]

    def sort_key(item):
        if sort_by is SortBy.rating:
            val = effective_rating(item)
        else:
            val = item.get(sort_by.value)
        if val is None:
            return (1, "")
        return (0, val)

    results.sort(key=sort_key, reverse=sort_desc)
    await cache_set(cache_key, results, ttl=30)
    return results

@app.post("/api/songs")
async def upload_item(payload: ItemPayload):
    item_id = str(uuid.uuid4())
    date_str = datetime.now().strftime("%Y-%m-%d")
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    item_type = payload.type if payload.type in STORAGE_MAP else "song"
    config = STORAGE_MAP[item_type]
    filename = f"{item_id}.json"
    full_path = f"{config['folder']}{filename}"
    
    meta = {
        "id": item_id,
        "name": payload.name,
        "author": payload.author,
        "date": date_str,
        "type": item_type,
        "description": payload.description,
        "filename": filename,
        "rating": payload.rating,
        "updated_at": now
    }
    
    payload.data["_cloud_meta"] = meta
    try:
        await run_io(_write_json_sync, full_path, payload.data)
        current = await asset_service.read_index(item_type)
        current.insert(0, meta)
        await asset_service.write_index(item_type, current)
        _log_event("upload_item", item_type=item_type, item_id=item_id, filename=filename)
        return {"success": True, "id": item_id}
    except Exception as e:
        raise HTTPException(500, f"Upload failed: {str(e)}")

@app.put("/api/songs/{item_id}")
async def update_item(item_id: str, payload: ItemPayload):
    item_type = payload.type if payload.type in STORAGE_MAP else "song"
    config = STORAGE_MAP[item_type]
    filename = f"{item_id}.json"
    full_path = f"{config['folder']}{filename}"
    date_str = datetime.now().strftime("%Y-%m-%d")
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    new_meta = {
        "id": item_id,
        "name": payload.name,
        "author": payload.author,
        "date": date_str,
        "type": item_type,
        "description": payload.description,
        "filename": filename,
        "rating": payload.rating,
        "updated_at": now
    }

    payload.data["_cloud_meta"] = new_meta
    try:
        await run_io(_write_json_sync, full_path, payload.data)
        current = await asset_service.read_index(item_type)
        existing_index = next((i for i, item in enumerate(current) if item.get("id") == item_id), -1)
        if existing_index != -1:
            current.pop(existing_index)
        current.insert(0, new_meta)
        await asset_service.write_index(item_type, current)
        _log_event("update_item", item_type=item_type, item_id=item_id, filename=filename)
        return {"success": True, "id": item_id, "action": "updated"}
    except Exception as e:
        raise HTTPException(500, f"Update failed: {str(e)}")

@app.get("/api/songs/{item_id}/meta")
async def get_item_metadata(item_id: str, type: Optional[str] = Query(None)):
    search_types = [type] if type else ["song", "pattern", "bank", "sample", "music", "shader", "image", "video"]
    for t in search_types:
        try:
            index_data = await asset_service.read_index(t)
            if isinstance(index_data, list):
                entry = next((item for item in index_data if item.get("id") == item_id), None)
                if entry:
                    return entry
        except Exception:
            continue
    raise HTTPException(404, "Item not found")

@app.get("/api/songs/{item_id}")
async def get_item(item_id: str, type: Optional[str] = Query(None)):
    search_types = [type] if type else ["song", "pattern", "bank", "sample", "music", "shader", "image", "video"]
    for t in search_types:
        if t == "shader":
            try:
                entry = next(
                    (item for item in await asset_service.read_index("shader") if item.get("id") == item_id),
                    None
                )
                if entry:
                    return entry
            except Exception:
                continue

        config = STORAGE_MAP.get(t)
        if not config:
            continue
        filepath = f"{config['folder']}{item_id}.json"
        blob = bucket.blob(filepath)
        exists = await run_io(blob.exists)
        if exists:
            data = await run_io(blob.download_as_text)
            return json.loads(data)
    raise HTTPException(404, "Item not found")

@app.patch("/api/songs/{item_id}")
async def patch_song(item_id: str, patch: MetaPatch):
    try:
        index = await asset_service.read_index("song")
        entry = next((e for e in index if e.get("id") == item_id), None)
        if not entry:
            raise HTTPException(status_code=404, detail="Song not found")

        changes = patch.model_dump(exclude_unset=True)
        if not changes:
            return {"status": "no-op", "message": "Nothing to update"}

        updated = {}
        for field, value in changes.items():
            if field == "tags":
                entry["tags"] = value if value is not None else []
            else:
                entry[field] = value
            updated[field] = entry[field]
        if "updated_at" not in changes:
            entry["updated_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            updated["updated_at"] = entry["updated_at"]

        await asset_service.write_index("song", index)
        _log_event("patch_song", item_id=item_id, updated=updated)
        return {"status": "success", "item_id": item_id, "updated": updated}
    except Exception as e:
        _log_event("patch_song_failed", level="error", item_id=item_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))

# ========================= SAMPLES =========================

@app.post("/api/samples")
async def upload_sample(
    file: UploadFile = File(...),
    author: str = Form(...),
    description: str = Form(""),
    rating: Optional[int] = Form(None)
):
    sample_id = str(uuid.uuid4())
    ext = os.path.splitext(file.filename)[1]
    storage_filename = f"{sample_id}{ext}"
    config = STORAGE_MAP["sample"]
    full_path = f"{config['folder']}{storage_filename}"
    
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    meta = {
        "id": sample_id,
        "name": file.filename,
        "author": author,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "type": "sample",
        "description": description,
        "filename": storage_filename,
        "rating": rating,
        "updated_at": now
    }
    
    try:
        blob = bucket.blob(full_path)
        await run_io(blob.upload_from_file, file.file, content_type=file.content_type)

        idx = await asset_service.read_index("sample")
        idx.insert(0, meta)
        await asset_service.write_index("sample", idx)
        _log_event("upload_sample", sample_id=sample_id, filename=storage_filename)
        return {"success": True, "id": sample_id}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/api/samples/{sample_id}")
async def get_sample(sample_id: str):
    config = STORAGE_MAP["sample"]
    idx = await asset_service.read_index("sample")
    entry = next((i for i in idx if i["id"] == sample_id), None)
    if not entry:
        raise HTTPException(404, "Sample not found")
    
    blob_path = f"{config['folder']}{entry['filename']}"
    blob = bucket.blob(blob_path)
    if not await run_io(blob.exists):
        raise HTTPException(404, "File missing")
    
    def iterfile():
        with blob.open("rb") as f:
            while chunk := f.read(1024 * 1024):
                yield chunk
    
    return StreamingResponse(
        iterfile(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename={entry['name']}"}
    )

@app.post("/api/samples/{sample_id}/play")
async def record_play(sample_id: str):
    config = STORAGE_MAP["sample"]
    index_path = config["index"]
    now = datetime.now().isoformat()
    
    async with INDEX_LOCK:
        try:
            index_data = await run_io(_read_json_sync, index_path)
            if not isinstance(index_data, list):
                raise HTTPException(500, "Index corrupted")
            
            entry = next((item for item in index_data if item.get("id") == sample_id), None)
            if not entry:
                raise HTTPException(404, "Sample not found")
            
            entry["last_played"] = now
            await run_io(_write_json_sync, index_path, index_data)
            await cache_delete("library:sample")
            await cache_delete("library:all")
            
            return {"success": True, "id": sample_id, "last_played": now}
        except HTTPException:
            raise
        except Exception as e:
            _log_event("record_sample_play_failed", level="error", sample_id=sample_id, error=str(e))
            raise HTTPException(500, f"Failed: {str(e)}")

@app.put("/api/samples/{sample_id}")
async def update_sample_metadata(sample_id: str, payload: SampleMetaUpdatePayload):
    try:
        index_data = await asset_service.read_index("sample")
        entry_idx = next((i for i, item in enumerate(index_data) if item.get("id") == sample_id), -1)
        if entry_idx == -1:
            raise HTTPException(404, "Sample not found")

        entry = index_data[entry_idx]
        update_happened = False

        if payload.name is not None and payload.name != entry.get("name"):
            entry["name"] = payload.name
            update_happened = True
        if payload.description is not None:
            entry["description"] = payload.description
            update_happened = True
        if payload.rating is not None:
            entry["rating"] = payload.rating
            update_happened = True
        if payload.genre is not None:
            entry["genre"] = payload.genre
            update_happened = True
        if payload.last_played is not None:
            entry["last_played"] = payload.last_played
            update_happened = True

        if update_happened:
            entry["updated_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            await asset_service.write_index("sample", index_data)
            _log_event("update_sample_metadata", sample_id=sample_id, action="metadata_updated")

        return {"success": True, "id": sample_id, "action": "metadata_updated" if update_happened else "no_change"}
    except HTTPException:
        raise
    except Exception as e:
        _log_event("update_sample_metadata_failed", level="error", sample_id=sample_id, error=str(e))
        raise HTTPException(500, f"Failed: {str(e)}")

# ========================= MUSIC =========================

@app.get("/api/music/{music_id}")
async def get_music_file(music_id: str):
    config = STORAGE_MAP["music"]
    idx = await asset_service.read_index("music")
    entry = next((i for i in idx if i["id"] == music_id), None)
    if not entry:
        raise HTTPException(404, "Music not found")
    
    blob_path = f"{config['folder']}{entry['filename']}"
    blob = bucket.blob(blob_path)
    if not await run_io(blob.exists):
        raise HTTPException(404, "File missing")
    
    def iterfile():
        with blob.open("rb") as f:
            while chunk := f.read(1024 * 1024):
                yield chunk
    
    lower_name = entry['filename'].lower()
    if lower_name.endswith('.flac'):
        media_type = 'audio/flac'
    elif lower_name.endswith('.wav'):
        media_type = 'audio/wav'
    elif lower_name.endswith('.mp3'):
        media_type = 'audio/mpeg'
    else:
        media_type = 'audio/mpeg'
    
    return StreamingResponse(
        iterfile(),
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{entry["name"]}"'}
    )

@app.put("/api/music/{music_id}")
async def update_music_metadata(music_id: str, payload: SampleMetaUpdatePayload):
    try:
        index_data = await asset_service.read_index("music")
        entry_idx = next((i for i, item in enumerate(index_data) if item.get("id") == music_id), -1)
        if entry_idx == -1:
            raise HTTPException(404, "Music not found")

        entry = index_data[entry_idx]
        update_happened = False

        if payload.name is not None:
            entry["name"] = payload.name
            update_happened = True
        if payload.rating is not None:
            entry["rating"] = payload.rating
            update_happened = True
        if payload.genre is not None:
            entry["genre"] = payload.genre
            update_happened = True
        if payload.description is not None:
            entry["description"] = payload.description
            update_happened = True

        if update_happened:
            entry["updated_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            await asset_service.write_index("music", index_data)
            _log_event("update_music_metadata", music_id=music_id, action="updated")

        return {"success": True, "id": music_id, "action": "updated" if update_happened else "no_change"}
    except HTTPException:
        raise
    except Exception as e:
        _log_event("update_music_metadata_failed", level="error", music_id=music_id, error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to update: {str(e)}")


# ========================= IMAGES =========================

@app.get("/api/images/{image_id}")
async def get_image_file(image_id: str):
    config = STORAGE_MAP["image"]
    idx = await run_io(_read_json_sync, config["index"])
    entry = next((i for i in idx if i["id"] == image_id), None)
    if not entry:
        raise HTTPException(404, "Image not found")

    blob_path = f"{config['folder']}{entry['filename']}"
    blob = bucket.blob(blob_path)
    if not await run_io(blob.exists):
        raise HTTPException(404, "File missing")

    def iterfile():
        with blob.open("rb") as f:
            while chunk := f.read(1024 * 1024):
                yield chunk

    lower_name = entry['filename'].lower()
    if lower_name.endswith('.png'):
        media_type = 'image/png'
    elif lower_name.endswith(('.jpg', '.jpeg')):
        media_type = 'image/jpeg'
    elif lower_name.endswith('.webp'):
        media_type = 'image/webp'
    elif lower_name.endswith('.gif'):
        media_type = 'image/gif'
    else:
        media_type = 'application/octet-stream'

    return StreamingResponse(
        iterfile(),
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{entry["name"]}"'}
    )

@app.put("/api/images/{image_id}")
async def update_image_metadata(image_id: str, payload: SampleMetaUpdatePayload):
    config = STORAGE_MAP["image"]
    index_path = config["index"]

    try:
        index_data = await run_io(_read_json_sync, index_path)
        if not isinstance(index_data, list):
            raise HTTPException(500, "Index corrupted")

        entry_idx = next((i for i, item in enumerate(index_data) if item.get("id") == image_id), -1)
        if entry_idx == -1:
            raise HTTPException(404, "Image not found")

        entry = index_data[entry_idx]
        update_happened = False

        if payload.name is not None:
            entry["name"] = payload.name
            update_happened = True
        if payload.rating is not None:
            entry["rating"] = payload.rating
            update_happened = True
        if payload.description is not None:
            entry["description"] = payload.description
            update_happened = True

        if update_happened:
            entry["updated_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            await asset_service.write_index("image", index_data)

        return {"success": True, "id": image_id, "action": "updated" if update_happened else "no_change"}
    except HTTPException:
        raise
    except Exception as e:
        _log_event("update_image_failed", level="error", image_id=image_id, error=str(e))
        raise HTTPException(500, f"Failed: {str(e)}")

# ========================= VIDEOS =========================

@app.get("/api/videos/{video_id}")
async def get_video_file(video_id: str):
    config = STORAGE_MAP["video"]
    idx = await run_io(_read_json_sync, config["index"])
    entry = next((i for i in idx if i["id"] == video_id), None)
    if not entry:
        raise HTTPException(404, "Video not found")

    blob_path = f"{config['folder']}{entry['filename']}"
    blob = bucket.blob(blob_path)
    if not await run_io(blob.exists):
        raise HTTPException(404, "File missing")

    def iterfile():
        with blob.open("rb") as f:
            while chunk := f.read(1024 * 1024):
                yield chunk

    lower_name = entry['filename'].lower()
    if lower_name.endswith('.mp4'):
        media_type = 'video/mp4'
    elif lower_name.endswith('.webm'):
        media_type = 'video/webm'
    elif lower_name.endswith('.mov'):
        media_type = 'video/quicktime'
    else:
        media_type = 'application/octet-stream'

    return StreamingResponse(
        iterfile(),
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{entry["name"]}"'}
    )

@app.put("/api/videos/{video_id}")
async def update_video_metadata(video_id: str, payload: SampleMetaUpdatePayload):
    config = STORAGE_MAP["video"]
    index_path = config["index"]

    try:
        index_data = await run_io(_read_json_sync, index_path)
        if not isinstance(index_data, list):
            raise HTTPException(500, "Index corrupted")

        entry_idx = next((i for i, item in enumerate(index_data) if item.get("id") == video_id), -1)
        if entry_idx == -1:
            raise HTTPException(404, "Video not found")

        entry = index_data[entry_idx]
        update_happened = False

        if payload.name is not None:
            entry["name"] = payload.name
            update_happened = True
        if payload.rating is not None:
            entry["rating"] = payload.rating
            update_happened = True
        if payload.description is not None:
            entry["description"] = payload.description
            update_happened = True

        if update_happened:
            entry["updated_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            await asset_service.write_index("video", index_data)

        return {"success": True, "id": video_id, "action": "updated" if update_happened else "no_change"}
    except HTTPException:
        raise
    except Exception as e:
        _log_event("update_video_failed", level="error", video_id=video_id, error=str(e))
        raise HTTPException(500, f"Failed: {str(e)}")

# ========================= ADMIN / SYNC =========================



@app.post("/api/admin/sync-images")
async def sync_images_folder():
    config = STORAGE_MAP["image"]
    report = {"added": 0, "removed": 0}

    async with INDEX_LOCK:
        try:
            blobs = await run_io(lambda: list(bucket.list_blobs(prefix=config["folder"])))
            media_files = []
            for b in blobs:
                fname = b.name.replace(config["folder"], "")
                if fname and not b.name.endswith(config["index"]):
                    lower = fname.lower()
                    if lower.endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif')):
                        media_files.append({
                            "filename": fname,
                            "name": fname,
                            "size": b.size,
                            "url": b.public_url
                        })

            index_data = await run_io(_read_json_sync, config["index"])
            if not isinstance(index_data, list):
                index_data = []

            index_map = {item["filename"]: item for item in index_data}
            disk_set = set(f["filename"] for f in media_files)

            new_index = [item for item in index_data if item["filename"] in disk_set]
            report["removed"] = len(index_data) - len(new_index)

            for file_info in media_files:
                if file_info["filename"] not in index_map:
                    new_entry = {
                        "id": str(uuid.uuid4()),
                        "filename": file_info["filename"],
                        "name": file_info["name"],
                        "type": "image",
                        "date": datetime.now().strftime("%Y-%m-%d"),
                        "author": "Unknown",
                        "description": "",
                        "rating": None,
                        "url": file_info["url"],
                        "size": file_info["size"]
                    }
                    new_index.insert(0, new_entry)
                    report["added"] += 1

            if report["added"] > 0 or report["removed"] > 0:
                await run_io(_write_json_sync, config["index"], new_index)
                await cache_delete("library:image")
                await cache_delete("library:all")

            report["total"] = len(new_index)
            return report
        except Exception as e:
            raise HTTPException(500, f"Failed to sync images: {str(e)}")

@app.post("/api/admin/sync-videos")
async def sync_videos_folder():
    config = STORAGE_MAP["video"]
    report = {"added": 0, "removed": 0}

    async with INDEX_LOCK:
        try:
            blobs = await run_io(lambda: list(bucket.list_blobs(prefix=config["folder"])))
            media_files = []
            for b in blobs:
                fname = b.name.replace(config["folder"], "")
                if fname and not b.name.endswith(config["index"]):
                    lower = fname.lower()
                    if lower.endswith(('.mp4', '.webm', '.mov')):
                        media_files.append({
                            "filename": fname,
                            "name": fname,
                            "size": b.size,
                            "url": b.public_url
                        })

            index_data = await run_io(_read_json_sync, config["index"])
            if not isinstance(index_data, list):
                index_data = []

            index_map = {item["filename"]: item for item in index_data}
            disk_set = set(f["filename"] for f in media_files)

            new_index = [item for item in index_data if item["filename"] in disk_set]
            report["removed"] = len(index_data) - len(new_index)

            for file_info in media_files:
                if file_info["filename"] not in index_map:
                    new_entry = {
                        "id": str(uuid.uuid4()),
                        "filename": file_info["filename"],
                        "name": file_info["name"],
                        "type": "video",
                        "date": datetime.now().strftime("%Y-%m-%d"),
                        "author": "Unknown",
                        "description": "",
                        "rating": None,
                        "url": file_info["url"],
                        "size": file_info["size"]
                    }
                    new_index.insert(0, new_entry)
                    report["added"] += 1

            if report["added"] > 0 or report["removed"] > 0:
                await run_io(_write_json_sync, config["index"], new_index)
                await cache_delete("library:video")
                await cache_delete("library:all")

            report["total"] = len(new_index)
            return report
        except Exception as e:
            raise HTTPException(500, f"Failed to sync videos: {str(e)}")

@app.post("/api/admin/sync")
async def sync_gcs_storage(type: Optional[str] = Query(None, alias="type")):
    """Sync one or more GCS asset folders back into index files.

    When no ?type= parameter is provided, all asset types except default and
    music are synchronized. Passing ?type=shader or ?type=music will target a
    single type explicitly.
    """
    requested_types = [t.strip() for t in type.split(",")] if type else []
    if requested_types:
        invalid = [t for t in requested_types if t not in STORAGE_MAP or t == "default"]
        if invalid:
            raise HTTPException(400, f"Unsupported sync type(s): {', '.join(invalid)}")
        sync_types = requested_types
    else:
        sync_types = [t for t in STORAGE_MAP if t != "default" and t != "music"]

    report = {}

    for item_type in sync_types:
        config = STORAGE_MAP[item_type]
        added = 0
        removed = 0

        try:
            blobs = await run_io(lambda: list(bucket.list_blobs(prefix=config["folder"])))
            actual_files = []

            for b in blobs:
                fname = b.name.replace(config["folder"], "")
                if not fname or b.name.endswith(config["index"]):
                    continue
                if item_type == "shader":
                    if "/" in fname:
                        continue
                    if fname.lower().endswith(".wgsl"):
                        actual_files.append(fname)
                else:
                    actual_files.append(fname)

            index_data = await asset_service.read_index(item_type)
            if not isinstance(index_data, list):
                index_data = []

            index_map = {item["filename"]: item for item in index_data}
            disk_set = set(actual_files)

            new_index = [item for item in index_data if item["filename"] in disk_set]
            removed = len(index_data) - len(new_index)
            for item in new_index:
                if "updated_at" not in item:
                    item["updated_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

            for filename in actual_files:
                if filename not in index_map:
                    if item_type == "shader":
                        shader_id = filename.replace(".wgsl", "")
                        new_entry = {
                            "id": shader_id,
                            "filename": filename,
                            "type": "shader",
                            "date": datetime.now().strftime("%Y-%m-%d"),
                            "name": shader_id.replace("-", " ").title(),
                            "author": "Unknown",
                            "description": "Auto-discovered",
                            "genre": None,
                            "last_played": None,
                            "stars": 0.0,
                            "rating_count": 0,
                            "play_count": 0,
                            "updated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "thumbnail": None
                        }
                        meta_blob = bucket.blob(f"{config['folder']}{shader_id}/metadata.json")
                        if await run_io(meta_blob.exists):
                            try:
                                meta_content = json.loads(await run_io(meta_blob.download_as_text))
                                new_entry.update({
                                    k: v for k, v in meta_content.items()
                                    if k in {
                                        "name", "author", "description", "tags",
                                        "coordinate", "stars", "rating_count",
                                        "play_count", "thumbnail", "updated_at"
                                    }
                                })
                            except Exception:
                                pass
                    else:
                        new_entry = {
                            "id": str(uuid.uuid4()),
                            "filename": filename,
                            "type": item_type,
                            "date": datetime.now().strftime("%Y-%m-%d"),
                            "name": filename,
                            "author": "Unknown",
                            "description": "Auto-discovered",
                            "genre": None,
                            "last_played": None,
                            "updated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
                        }

                        if filename.endswith(".json") and item_type in ["song", "pattern", "bank"]:
                            try:
                                b = bucket.blob(f"{config['folder']}{filename}")
                                content = json.loads(await run_io(b.download_as_text))
                                if "name" in content:
                                    new_entry["name"] = content["name"]
                                if "author" in content:
                                    new_entry["author"] = content["author"]
                            except Exception:
                                pass

                    new_index.insert(0, new_entry)
                    added += 1

            if added > 0 or removed > 0:
                await asset_service.write_index(item_type, new_index)

            report[item_type] = {"added": added, "removed": removed, "status": "synced"}
        except Exception as e:
            report[item_type] = {"error": str(e)}

    await cache_clear()
    _log_event("sync_gcs_storage", report=report)
    return report

@app.post("/api/admin/seed-test-samples")
async def seed_test_samples():
    config = STORAGE_MAP["sample"]
    test_samples = [
        {"id": "test-flac-001", "name": "Test Ambient Track.flac", "filename": "test-flac-001.flac",
         "type": "sample", "author": "Test Artist", "date": "2024-02-09", "description": "Test ambient", "rating": 8, "genre": "ambient"},
        {"id": "test-wav-002", "name": "Test Bass Line.wav", "filename": "test-wav-002.wav",
         "type": "sample", "author": "Test Artist", "date": "2024-02-09", "description": "Test bass", "rating": 7, "genre": "bass"},
        {"id": "test-flac-003", "name": "Unrated Demo.flac", "filename": "test-flac-003.flac",
         "type": "sample", "author": "Unknown", "date": "2024-02-09", "description": "Demo", "rating": None, "genre": None}
    ]
    
    async with INDEX_LOCK:
        try:
            index_data = await run_io(_read_json_sync, config["index"])
            if not isinstance(index_data, list):
                index_data = []
            
            existing_ids = {item.get("id") for item in index_data}
            added = 0
            for sample in test_samples:
                if sample["id"] not in existing_ids:
                    index_data.insert(0, sample)
                    added += 1
            
            await run_io(_write_json_sync, config["index"], index_data)
            await cache_delete("library:sample")
            await cache_delete("library:all")
            return {"success": True, "added": added, "total": len(index_data)}
        except Exception as e:
            raise HTTPException(500, f"Failed to seed: {str(e)}")

@app.post("/api/admin/seed-brainfuck-examples")
async def seed_brainfuck_examples():
    config = STORAGE_MAP["brainfuck"]
    examples = [
        {"id": "bf-mandelbrot", "name": "Mandelbrot Set", "type": "brainfuck", "author": "Classic BF",
         "date": "2026-03-07", "description": "bf2wasm + -O3", "filename": "mandelbrot.bf",
         "execution_time_ms": 1247, "cells": 30000, "relative_to_cpp": 0.26, "relative_to_js": 1.48},
        {"id": "bf-fib", "name": "Fibonacci n=40", "type": "brainfuck", "author": "Classic BF",
         "date": "2026-03-07", "description": "fuck compiler", "filename": "fib.bf",
         "execution_time_ms": 47, "cells": 30000, "relative_to_cpp": 0.17, "relative_to_js": 2.1},
        {"id": "bf-sieve", "name": "Prime Sieve n=1M", "type": "brainfuck", "author": "Classic BF",
         "date": "2026-03-07", "description": "brainfuck2wasm", "filename": "sieve.bf",
         "execution_time_ms": 184, "cells": 16384, "relative_to_cpp": 0.22, "relative_to_js": 1.9}
    ]
    
    async with INDEX_LOCK:
        idx = await run_io(_read_json_sync, config["index"]) or []
        existing_ids = {item.get("id") for item in idx}
        added = 0
        for ex in examples:
            if ex["id"] not in existing_ids:
                idx.insert(0, ex)
                added += 1
        await run_io(_write_json_sync, config["index"], idx)
        await cache_delete("library:brainfuck")
        await cache_delete("library:all")
        return {"success": True, "added": added, "total": len(idx)}

# ========================= STORAGE LISTING =========================

@app.get("/api/storage/files")
async def list_gcs_folder(folder: str = Query(..., description="Folder name, e.g., 'songs' or 'samples'")):
    config = STORAGE_MAP.get(folder)
    prefix = config["folder"] if config else f"{folder}/"
    
    try:
        def _fetch_blobs():
            blobs = bucket.list_blobs(prefix=prefix, delimiter="/")
            file_list = []
            for blob in blobs:
                name = blob.name.replace(prefix, "")
                if name:
                    file_list.append({
                        "filename": name,
                        "size": blob.size,
                        "updated": blob.updated.isoformat() if blob.updated else None,
                        "url": blob.public_url if blob.public_url else None
                    })
            return file_list
        
        files = await run_io(_fetch_blobs)
        return {"folder": prefix, "count": len(files), "files": files}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/shaders/categories")
async def list_categories():
    """Return the category hierarchy for UI rendering."""
    return {
        "groups": CATEGORY_GROUPS,
        "all_categories": [c.value for c in ShaderCategory]
    }
# ========================= FTP BRIDGE ENDPOINTS =========================

@app.head("/api/shaders/{shader_id}/wgsl")
@app.get("/api/shaders/{shader_id}/wgsl", response_class=PlainTextResponse)
async def get_shader_wgsl(shader_id: str):
    """Returns raw WGSL text for direct consumption by WebGPU renderer.
    Resolution order: memory cache → GCS bucket → FTP server."""
    cache_key = f"shader_wgsl:{shader_id}"
    cached = await cache_get(cache_key)
    if cached:
        return PlainTextResponse(cached, media_type="text/plain")

    # Try GCS
    config = STORAGE_MAP["shader"]
    blob_path = f"{config['folder']}{shader_id}.wgsl"
    blob = bucket.blob(blob_path)
    if await run_io(blob.exists):
        code = await run_io(blob.download_as_text)
        await cache_set(cache_key, code, ttl=3600)
        return PlainTextResponse(code, media_type="text/plain")

    # Fallback to FTP
    if FTP_ENABLED:
        try:
            code = await run_io(_fetch_ftp_file_sync, f"{shader_id}.wgsl")
            await cache_set(cache_key, code, ttl=3600)
            return PlainTextResponse(code, media_type="text/plain")
        except Exception:
            pass

    raise HTTPException(404, f"Shader {shader_id} not found")

@app.get("/api/ftp/shaders/{filename}")
async def get_ftp_shader(filename: str):
    """Fetch a shader directly from FTP with cache. Returns JSON with code and metadata."""
    if not FTP_ENABLED:
        raise HTTPException(503, "FTP not configured")
    if not filename.endswith(".wgsl"):
        filename += ".wgsl"
    cache_key = f"ftp_shader_code:{filename}"
    cached = await cache_get(cache_key)
    if cached:
        return {"source": "cache", "filename": filename, "code": cached}
    try:
        code = await run_io(_fetch_ftp_file_sync, filename)
        await cache_set(cache_key, code, ttl=3600)
        return {"source": "ftp", "filename": filename, "code": code}
    except Exception as e:
            _log_event("ftp_fetch_failed", level="error", filename=filename, error=str(e))

@app.post("/api/admin/sync-ftp-to-gcs")
async def sync_ftp_to_gcs():
    """Scan FTP directory and import missing .wgsl shaders into GCS bucket."""
    if not FTP_ENABLED:
        raise HTTPException(503, "FTP not configured")
    config = STORAGE_MAP["shader"]
    report = {"added": 0, "skipped": 0, "errors": []}

    async with INDEX_LOCK:
        try:
            ftp_files = await run_io(_list_ftp_files_sync)
            index = await run_io(_read_json_sync, config["index"])
            if not isinstance(index, list):
                index = []
            existing = {item.get("filename", "") for item in index}

            for fname in ftp_files:
                if fname in existing:
                    report["skipped"] += 1
                    continue
                try:
                    code = await run_io(_fetch_ftp_file_sync, fname)
                    blob = bucket.blob(f"{config['folder']}{fname}")
                    await run_io(blob.upload_from_string, code, content_type="text/plain")
                    shader_id = fname.replace(".wgsl", "")
                    index.insert(0, {
                        "id": shader_id,
                        "name": shader_id.replace("-", " ").title(),
                        "filename": fname,
                        "author": "ftp-import",
                        "date": datetime.now().strftime("%Y-%m-%d"),
                        "type": "shader",
                        "description": "Imported from FTP",
                        "tags": ["ftp-import"],
                        "stars": 0.0,
                        "rating_count": 0,
                        "play_count": 0
                    })
                    report["added"] += 1
                except Exception as e:
                    _log_event("ftp_import_failed", level="error", filename=fname, error=str(e))
                    report["errors"].append({"file": fname, "error": str(e)})

            if report["added"] > 0:
                await run_io(_write_json_sync, config["index"], index)
            await cache_clear()
        except Exception as e:
            raise HTTPException(500, f"FTP sync failed: {str(e)}")

    report["total"] = len(index)
    return report

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7860)
