from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, JSONResponse, HTMLResponse
from fastapi import Request, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
import logging
import os
from pathlib import Path

from .config import settings
from .cors import build_cors_middleware_options

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
from .webhooks import webhook_router, files_router
from .api import api_router
from .models_router import models_router
from .audio_router import audio_router
from .leaderboard_router import leaderboard_router
from .adventure_router import adventure_router
from .sequencer_router import sequencer_router
from .vps_browser_router import vps_browser_router
from .notes_router import notes_router
from .pachinball_router import pachinball_router
from .mod_router import mod_router
from .presets_router import presets_router
from .file_watcher import start_watching
from . import presets


# === SSH-POWERED ADMIN PANEL (SIMPLE VERSION - NO TEMPLATES) ===
import asyncssh
import asyncio
import uuid
from typing import Dict



# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Contabo Storage Manager",
    description="Webhook bridge + static file server for image-effects, flac_player, and web_sequencer",
    version="1.0.0",
)

# Enhanced CORS middleware - MUST be added before routers
# CORSMiddleware must ALWAYS be installed so OPTIONS preflights are intercepted
# at the middleware layer. Without it, router paths return 405 before the
# catch-all @app.options route is reached.
app.add_middleware(
    CORSMiddleware,
    **build_cors_middleware_options(
        settings.cors_origins,
        settings.cors_origin_regex,
    ),
)

@app.on_event("startup")
async def startup_event():
    logger.info("Starting file watcher for %s", settings.files_dir)
    start_watching(settings.files_dir)
    presets.load_index()
    stats = presets.get_index_stats()
    logger.info(
        "Preset index on startup: %s presets across %s dirs",
        stats.get("total", 0),
        len(stats.get("dirs", {})),
    )


# Include routers
app.include_router(webhook_router)
app.include_router(files_router)        # ← Static files router
app.include_router(api_router)          # ← API endpoints for shaders, images, ratings
app.include_router(models_router)       # ← Model serving with range header support
app.include_router(audio_router)        # ← Audio endpoints for music and samples (router already has /api prefix)
app.include_router(leaderboard_router)  # ← Leaderboard endpoints for high scores (router already has /api prefix)
app.include_router(adventure_router)    # ← Adventure mode endpoints for level progress
app.include_router(sequencer_router)    # ← Sequencer endpoints for songs/patterns/banks/samples
app.include_router(vps_browser_router)  # ← VPS file browser endpoints
app.include_router(notes_router)        # ← Named notes endpoints
app.include_router(pachinball_router)   # ← Pachinball game content endpoints (router already has /api prefix)
app.include_router(mod_router)          # ← MOD music file endpoints (/api/mods)
app.include_router(presets_router)      # ← MilkDrop preset endpoints (/api/presets)

@app.get("/", response_class=HTMLResponse)
async def media_gallery():
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>storage.noahcohn.com</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');
            body { font-family: 'Inter', system-ui, sans-serif; }
            .gallery-grid { 
                display: grid;
                grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
                gap: 20px;
            }
            .media-card {
                transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            }
            .media-card:hover {
                transform: translateY(-4px);
                box-shadow: 0 20px 25px -5px rgb(0 0 0 / 0.1), 0 8px 10px -6px rgb(0 0 0 / 0.1);
            }
        </style>
    </head>
    <body class="bg-[#0a0a0a] text-gray-200 min-h-screen">
        <div class="max-w-7xl mx-auto p-8">
            <!-- Header -->
            <div class="flex justify-between items-center mb-10">
                <div>
                    <h1 class="text-4xl font-semibold bg-gradient-to-r from-cyan-400 to-pink-500 bg-clip-text text-transparent">
                        storage.noahcohn.com
                    </h1>
                    <p class="text-gray-500">Google Cloud &bull; Media &amp; Shader Vault</p>
                </div>
                <div class="flex gap-4">
                    <button onclick="uploadFile()" 
                            class="px-6 py-3 bg-white text-black rounded-2xl font-medium flex items-center gap-2 hover:bg-cyan-400 hover:text-black transition">
                        <i class="fas fa-upload"></i> Upload
                    </button>
                </div>
            </div>

            <!-- Filters -->
            <div class="flex gap-2 mb-8 flex-wrap" id="filters">
                <!-- Populated by JS -->
            </div>

            <!-- Search -->
            <div class="relative mb-8">
                <input type="text" id="search" 
                       placeholder="Search files, shaders, videos..." 
                       class="w-full bg-[#111] border border-gray-800 rounded-3xl px-6 py-4 pl-12 text-lg focus:outline-none focus:border-cyan-500"
                       onkeyup="if(event.key==='Enter') filterGallery()">
                <i class="fas fa-search absolute left-6 top-5 text-gray-500"></i>
            </div>

            <!-- Gallery Grid -->
            <div class="gallery-grid" id="gallery">
                <!-- Populated by JS from API -->
            </div>
        </div>

        <script>
            async function loadGallery() {
                const res = await fetch('/api/health');
                const data = await res.json();
                
                // For now we show stats + placeholder cards
                // You can later connect real /api/storage/files and /api/shaders endpoints
                const gallery = document.getElementById('gallery');
                gallery.innerHTML = `
                    <div class="col-span-full text-center py-12 text-gray-500">
                        <p class="text-xl">Media Gallery coming soon...</p>
                        <p class="mt-2">We'll pull from /api/storage/files and /api/shaders automatically</p>
                    </div>
                `;
                // TODO: Later expand this to fetch real media
            }

            // Simple filter chips
            function createFilters() {
                const filtersHTML = `
                    <button onclick="setFilter('all')" class="px-5 py-2 rounded-3xl bg-white/10 hover:bg-white/20 transition">All Media</button>
                    <button onclick="setFilter('shader')" class="px-5 py-2 rounded-3xl bg-white/10 hover:bg-white/20 transition flex items-center gap-2"><i class="fas fa-cube"></i> Shaders</button>
                    <button onclick="setFilter('video')" class="px-5 py-2 rounded-3xl bg-white/10 hover:bg-white/20 transition flex items-center gap-2"><i class="fas fa-video"></i> Videos</button>
                    <button onclick="setFilter('audio')" class="px-5 py-2 rounded-3xl bg-white/10 hover:bg-white/20 transition flex items-center gap-2"><i class="fas fa-music"></i> Audio</button>
                    <button onclick="setFilter('image')" class="px-5 py-2 rounded-3xl bg-white/10 hover:bg-white/20 transition flex items-center gap-2"><i class="fas fa-image"></i> Images</button>
                    <button onclick="setFilter('note')" class="px-5 py-2 rounded-3xl bg-white/10 hover:bg-white/20 transition flex items-center gap-2"><i class="fas fa-note-sticky"></i> Notes</button>
                `;
                document.getElementById('filters').innerHTML = filtersHTML;
            }

            function setFilter(type) {
                // Future filter logic
                console.log('Filter:', type);
                loadGallery();
            }

            function uploadFile() {
                window.location.href = '/admin';
            }

            // Init
            window.onload = () => {
                createFilters();
                loadGallery();
            };
        </script>
    </body>
    </html>
    """

@app.get("/health")
async def health_check():
    # Verify storage is writable
    storage_ok = True
    storage_error = None
    try:
        test_path = Path(settings.files_dir) / ".healthcheck"
        test_path.write_text("ok")
        test_path.unlink()
    except Exception as exc:
        storage_ok = False
        storage_error = str(exc)

    return {
        "status": "ok" if storage_ok else "error",
        "service": "contabo-storage-manager",
        "storage": {
            "writable": storage_ok,
            "path": settings.files_dir,
            "error": storage_error,
        },
    }


# Redirect routes for backwards compatibility (game calls these without /api prefix)
@app.get("/music")
async def music_redirect(request: Request):
    """Redirect /music to /api/music for backwards compatibility."""
    from fastapi.responses import RedirectResponse
    query = request.query_params
    return RedirectResponse(url=f"/api/music?{query}", status_code=307)


@app.get("/leaderboard")
async def leaderboard_redirect(request: Request):
    """Redirect /leaderboard to /api/leaderboard for backwards compatibility."""
    from fastapi.responses import RedirectResponse
    query = request.query_params
    return RedirectResponse(url=f"/api/leaderboard?{query}", status_code=307)

# Global CORS response handler - adds headers only when missing so we don't
# duplicate values already set by CORSMiddleware.
@app.middleware("http")
async def add_cors_headers(request: Request, call_next):
    response = await call_next(request)
    origin = request.headers.get("origin", "*")
    if "access-control-allow-origin" not in response.headers:
        response.headers["Access-Control-Allow-Origin"] = origin if origin else "*"
    if "access-control-allow-credentials" not in response.headers:
        response.headers["Access-Control-Allow-Credentials"] = "true"
    if "vary" not in response.headers:
        response.headers["Vary"] = "Origin"
    return response

# ── CONFIG ──
SSH_HOST = os.environ.get("BUILD_VPS_HOST", "code.noahcohn.com")
SSH_USER = os.environ.get("BUILD_VPS_USER", "root")
SSH_KEY_PATH = os.environ.get("BUILD_VPS_SSH_KEY", "/root/.ssh/id_ed25519")

active_tasks: Dict[str, dict] = {}

# Whitelisted commands (runs on code.noahcohn.com)
ALLOWED_COMMANDS = {
    "git-pull": "cd ~/contabo_storage_manager && git pull origin main",
    "npm-install": "cd ~/contabo_storage_manager && npm i",
    "npm-build": "cd ~/contabo_storage_manager && npm run build",
    "restart-service": "cd ~/contabo_storage_manager && docker compose --profile python restart",
    "sync-indexes": "curl -X POST http://localhost:8000/api/admin/sync",
    "sync-music": "curl -X POST http://localhost:8000/api/admin/sync-music",
}

@app.get("/admin", response_class=HTMLResponse)
async def admin_panel(request: Request):
    template_path = Path(__file__).parent / "templates" / "admin.html"
    if not template_path.exists():
        logger.error(f"Admin template not found at {template_path}")
        raise HTTPException(status_code=404, detail="Admin template not found")
    html = template_path.read_text(encoding="utf-8")
    return HTMLResponse(content=html)

@app.post("/api/admin/sync-music")
async def sync_music_admin(background_tasks: BackgroundTasks):
    """Trigger a background sync of music files from GCS to local storage."""
    import subprocess
    import sys

    def run_sync():
        script_path = Path(__file__).parent.parent.parent.parent / "scripts" / "sync_gcs_music.py"
        # Fallback for Docker layout where repo root is /app
        if not script_path.exists():
            script_path = Path("/app/scripts/sync_gcs_music.py")
        result = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True,
            text=True,
        )
        logger.info(f"Sync music exit code: {result.returncode}")
        if result.stdout:
            logger.info(result.stdout)
        if result.stderr:
            logger.error(result.stderr)

    background_tasks.add_task(run_sync)
    return {"success": True, "message": "Music sync started in background"}


@app.post("/api/admin/run")
async def run_remote_command(command_key: str, background_tasks: BackgroundTasks):
    if command_key not in ALLOWED_COMMANDS:
        raise HTTPException(400, f"Command '{command_key}' not allowed")
    
    task_id = str(uuid.uuid4())
    cmd = ALLOWED_COMMANDS[command_key]
    
    async def execute_via_ssh():
        active_tasks[task_id] = {"output": [], "status": "running", "cmd": cmd}
        try:
            async with asyncssh.connect(
                SSH_HOST, username=SSH_USER, client_keys=[SSH_KEY_PATH], known_hosts=None
            ) as conn:
                result = await conn.run(cmd, check=False)
                for line in (result.stdout or "").splitlines():
                    active_tasks[task_id]["output"].append(line)
                for line in (result.stderr or "").splitlines():
                    active_tasks[task_id]["output"].append(f"ERROR: {line}")
                active_tasks[task_id]["status"] = "success" if result.exit_status == 0 else "failed"
                active_tasks[task_id]["exit_code"] = result.exit_status
        except Exception as e:
            active_tasks[task_id]["output"].append(f"SSH ERROR: {str(e)}")
            active_tasks[task_id]["status"] = "failed"
    
    background_tasks.add_task(execute_via_ssh)
    return {"task_id": task_id, "command": cmd}

@app.get("/api/admin/logs/{task_id}")
async def stream_remote_logs(task_id: str):
    async def event_generator():
        last_index = 0
        while True:
            if task_id not in active_tasks:
                yield "data: [Task not found]\n\n"
                break
            task = active_tasks[task_id]
            for line in task["output"][last_index:]:
                yield f"data: {line}\n\n"
            last_index = len(task["output"])
            
            if task["status"] != "running":
                yield f"data: --- FINISHED (exit code {task.get('exit_code', 'unknown')}) ---\n\n"
                break
            await asyncio.sleep(0.3)
    
    return StreamingResponse(event_generator(), media_type="text/event-stream")
    
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
