from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, JSONResponse, HTMLResponse
from fastapi import Request, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
import logging
import os

from .config import settings
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
ALLOWED_ORIGINS = [
    "https://code.noahcohn.com",
    "https://storage.noahcohn.com",
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:8000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=86400,
)

# Explicit OPTIONS handler for all paths (handles preflight at nginx level too)
@app.options("/{path:path}")
async def handle_options(path: str, request: Request):
    """Handle CORS preflight requests for all paths."""
    origin = request.headers.get("origin", "*")
    return Response(
        status_code=204,
        headers={
            "Access-Control-Allow-Origin": origin if origin else "*",
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS, PATCH",
            "Access-Control-Allow-Headers": "DNT,User-Agent,X-Requested-With,If-Modified-Since,Cache-Control,Content-Type,Range,Authorization,X-Hub-Signature-256",
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Max-Age": "86400",
            "Vary": "Origin",
        }
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
                    <p class="text-gray-500">Google Cloud • Media &amp; Shader Vault</p>
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
                alert("Upload modal would go here (we can make a nice drag-and-drop modal next)");
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
    return {"status": "ok", "service": "contabo-storage-manager"}


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

# Global CORS response handler - ensures all responses have CORS headers
@app.middleware("http")
async def add_cors_headers(request: Request, call_next):
    response = await call_next(request)
    origin = request.headers.get("origin", "*")
    if origin:
        response.headers["Access-Control-Allow-Origin"] = origin
    else:
        response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Credentials"] = "true"
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
    html = """
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8">
      <title>1ink Control Panel</title>
      <script src="https://cdn.tailwindcss.com"></script>
      <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
      <style>
        body { font-family: system-ui; }
        .log { font-family: monospace; line-height: 1.5; white-space: pre-wrap; }
        .file-item:hover { background-color: rgba(255,255,255,0.05); }
        .breadcrumb:hover { text-decoration: underline; }
      </style>
    </head>
    <body class="bg-zinc-950 text-white p-8">
      <div class="max-w-6xl mx-auto">
        <h1 class="text-4xl font-bold mb-8">🚀 1ink Control Panel</h1>
        
        <!-- Tabs -->
        <div class="flex gap-2 mb-6 border-b border-zinc-800">
          <button onclick="showTab('commands')" id="tab-commands" class="px-6 py-3 border-b-2 border-emerald-500 text-emerald-400 font-medium">Commands</button>
          <button onclick="showTab('files')" id="tab-files" class="px-6 py-3 border-b-2 border-transparent text-zinc-400 hover:text-white font-medium">File Browser</button>
        </div>
        
        <!-- Commands Tab -->
        <div id="panel-commands" class="tab-panel">
          <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
            <button onclick="runCommand('git-pull')" 
                    class="bg-emerald-600 hover:bg-emerald-700 px-6 py-4 rounded-xl text-lg font-medium">Git Pull</button>
            <button onclick="runCommand('npm-install')" 
                    class="bg-emerald-600 hover:bg-emerald-700 px-6 py-4 rounded-xl text-lg font-medium">NPM Install</button>
            <button onclick="runCommand('npm-build')" 
                    class="bg-blue-600 hover:bg-blue-700 px-6 py-4 rounded-xl text-lg font-medium">NPM Run Build</button>
            <button onclick="runCommand('restart-service')" 
                    class="bg-amber-600 hover:bg-amber-700 px-6 py-4 rounded-xl text-lg font-medium">Restart Service</button>
            <button onclick="runCommand('sync-indexes')" 
                    class="bg-purple-600 hover:bg-purple-700 px-6 py-4 rounded-xl text-lg font-medium">Sync Indexes</button>
          </div>

          <div class="flex justify-between items-center mb-3">
            <h2 class="text-xl font-semibold">Live Output</h2>
            <button onclick="clearLog()" 
                    class="text-sm px-4 py-2 bg-zinc-800 hover:bg-zinc-700 rounded-lg">Clear Log</button>
          </div>

          <div id="log-window" class="bg-black border border-zinc-800 rounded-2xl p-6 h-96 overflow-auto text-emerald-400 log">
            Click any button above — live logs will stream here...
          </div>
        </div>
        
        <!-- File Browser Tab -->
        <div id="panel-files" class="tab-panel hidden">
          <!-- Breadcrumb & Actions -->
          <div class="flex justify-between items-center mb-4">
            <div id="breadcrumb" class="flex items-center gap-2 text-sm text-zinc-400">
              <span class="text-zinc-500">Loading...</span>
            </div>
            <div class="flex gap-2">
              <button onclick="refreshFiles()" class="px-4 py-2 bg-zinc-800 hover:bg-zinc-700 rounded-lg text-sm">
                <i class="fas fa-refresh mr-2"></i>Refresh
              </button>
              <button onclick="document.getElementById('file-input').click()" class="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 rounded-lg text-sm">
                <i class="fas fa-upload mr-2"></i>Upload
              </button>
              <input type="file" id="file-input" class="hidden" onchange="uploadFile(this.files[0])">
            </div>
          </div>
          
          <!-- File List -->
          <div class="bg-zinc-900 border border-zinc-800 rounded-2xl overflow-hidden">
            <div class="grid grid-cols-12 gap-4 px-6 py-3 bg-zinc-800 text-sm font-medium text-zinc-400">
              <div class="col-span-6">Name</div>
              <div class="col-span-2">Size</div>
              <div class="col-span-3">Modified</div>
              <div class="col-span-1 text-right">Actions</div>
            </div>
            <div id="file-list" class="divide-y divide-zinc-800">
              <div class="px-6 py-8 text-center text-zinc-500">Loading files...</div>
            </div>
          </div>
          
          <!-- File Preview Modal -->
          <div id="preview-modal" class="fixed inset-0 bg-black/80 hidden items-center justify-center z-50 p-4">
            <div class="bg-zinc-900 border border-zinc-700 rounded-2xl max-w-4xl w-full max-h-[90vh] flex flex-col">
              <div class="flex justify-between items-center px-6 py-4 border-b border-zinc-800">
                <h3 id="preview-title" class="text-lg font-semibold">File Preview</h3>
                <button onclick="closePreview()" class="text-zinc-400 hover:text-white">
                  <i class="fas fa-times text-xl"></i>
                </button>
              </div>
              <div id="preview-content" class="p-6 overflow-auto flex-1">
                <!-- Content loaded here -->
              </div>
              <div class="flex justify-end gap-2 px-6 py-4 border-t border-zinc-800">
                <button onclick="downloadCurrentFile()" class="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg">
                  <i class="fas fa-download mr-2"></i>Download
                </button>
              </div>
            </div>
          </div>
          
          <!-- Upload Progress -->
          <div id="upload-progress" class="fixed bottom-4 right-4 bg-zinc-900 border border-zinc-700 rounded-xl p-4 hidden">
            <div class="flex items-center gap-3">
              <i class="fas fa-spinner fa-spin text-emerald-400"></i>
              <span id="upload-text">Uploading...</span>
            </div>
          </div>
        </div>
      </div>

      <script>
        // Tab switching
        function showTab(tab) {
          document.querySelectorAll('.tab-panel').forEach(p => p.classList.add('hidden'));
          document.querySelectorAll('[id^="tab-"]').forEach(t => {
            t.classList.remove('border-emerald-500', 'text-emerald-400');
            t.classList.add('border-transparent', 'text-zinc-400');
          });
          document.getElementById(`panel-${tab}`).classList.remove('hidden');
          document.getElementById(`tab-${tab}`).classList.remove('border-transparent', 'text-zinc-400');
          document.getElementById(`tab-${tab}`).classList.add('border-emerald-500', 'text-emerald-400');
          
          if (tab === 'files' && currentPath === null) {
            loadFiles('');
          }
        }
        
        // Command execution
        async function runCommand(key) {
          const logWindow = document.getElementById('log-window');
          logWindow.innerHTML += `<div class="text-amber-400">[Starting ${key}...]</div>`;

          try {
            const res = await fetch(`/api/admin/run?command_key=${key}`, { method: 'POST' });
            if (!res.ok) {
              const text = await res.text();
              logWindow.innerHTML += `<div class="text-red-400">ERROR ${res.status}: ${text}</div>`;
              return;
            }
            const data = await res.json();
            logWindow.innerHTML += `<div class="text-blue-400">Task started → ${data.task_id}</div>`;

            const eventSource = new EventSource(`/api/admin/logs/${data.task_id}`);
            eventSource.onmessage = function(e) {
              if (e.data.includes("FINISHED")) {
                logWindow.innerHTML += `<div class="text-violet-400">${e.data}</div>`;
                eventSource.close();
              } else {
                logWindow.innerHTML += `<div>${e.data}</div>`;
              }
              logWindow.scrollTop = logWindow.scrollHeight;
            };
          } catch (err) {
            logWindow.innerHTML += `<div class="text-red-400">Failed to start command: ${err}</div>`;
          }
        }

        function clearLog() {
          document.getElementById('log-window').innerHTML = '';
        }
        
        // File Browser
        let currentPath = null;
        let currentFiles = [];
        
        function formatBytes(bytes) {
          if (bytes === 0) return '-';
          const k = 1024;
          const sizes = ['B', 'KB', 'MB', 'GB'];
          const i = Math.floor(Math.log(bytes) / Math.log(k));
          return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
        }
        
        function formatDate(timestamp) {
          return new Date(timestamp * 1000).toLocaleString();
        }
        
        function getIconForMime(mime) {
          if (mime.startsWith('image/')) return 'fa-image text-purple-400';
          if (mime.startsWith('audio/')) return 'fa-music text-cyan-400';
          if (mime.startsWith('video/')) return 'fa-video text-pink-400';
          if (mime.includes('json')) return 'fa-code text-yellow-400';
          if (mime.includes('text')) return 'fa-file-lines text-zinc-400';
          return 'fa-file text-zinc-500';
        }
        
        async function loadFiles(path) {
          currentPath = path;
          const listEl = document.getElementById('file-list');
          listEl.innerHTML = '<div class="px-6 py-8 text-center text-zinc-500">Loading...</div>';
          
          try {
            const res = await fetch(`/api/vps/browse?path=${encodeURIComponent(path)}`);
            if (!res.ok) throw new Error(await res.text());
            
            currentFiles = await res.json();
            
            // Update breadcrumb
            const parts = path.split('/').filter(p => p);
            let breadcrumbHtml = `<button onclick="loadFiles('')" class="breadcrumb text-emerald-400 hover:text-emerald-300"><i class="fas fa-home mr-1"></i>Home</button>`;
            let buildPath = '';
            parts.forEach((part, i) => {
              buildPath += (buildPath ? '/' : '') + part;
              breadcrumbHtml += `<span class="text-zinc-600 mx-2">/</span><button onclick="loadFiles('${buildPath}')" class="breadcrumb text-emerald-400 hover:text-emerald-300">${part}</button>`;
            });
            document.getElementById('breadcrumb').innerHTML = breadcrumbHtml || '<span class="text-zinc-500">Home</span>';
            
            // Render files
            if (currentFiles.length === 0) {
              listEl.innerHTML = '<div class="px-6 py-8 text-center text-zinc-500">Empty directory</div>';
              return;
            }
            
            listEl.innerHTML = currentFiles.map(file => `
              <div class="file-item grid grid-cols-12 gap-4 px-6 py-3 items-center cursor-pointer transition-colors">
                <div class="col-span-6 flex items-center gap-3" onclick="${file.type === 'dir' ? `loadFiles('${file.path}')` : `previewFile('${file.path}', '${file.name}', '${file.mime}')`}">
                  <i class="fas ${file.type === 'dir' ? 'fa-folder text-yellow-400' : getIconForMime(file.mime)} text-lg"></i>
                  <span class="truncate" title="${file.name}">${file.name}</span>
                </div>
                <div class="col-span-2 text-sm text-zinc-400">${formatBytes(file.size)}</div>
                <div class="col-span-3 text-sm text-zinc-500">${formatDate(file.modified)}</div>
                <div class="col-span-1 text-right">
                  ${file.type === 'file' ? `
                    <button onclick="event.stopPropagation(); downloadFile('${file.path}')" class="text-zinc-400 hover:text-emerald-400 px-2" title="Download">
                      <i class="fas fa-download"></i>
                    </button>
                  ` : ''}
                </div>
              </div>
            `).join('');
          } catch (err) {
            listEl.innerHTML = `<div class="px-6 py-8 text-center text-red-400">Error: ${err.message}</div>`;
          }
        }
        
        function refreshFiles() {
          if (currentPath !== null) loadFiles(currentPath);
        }
        
        async function uploadFile(file) {
          if (!file) return;
          
          const progressEl = document.getElementById('upload-progress');
          const textEl = document.getElementById('upload-text');
          progressEl.classList.remove('hidden');
          textEl.textContent = `Uploading ${file.name}...`;
          
          const formData = new FormData();
          formData.append('file', file);
          formData.append('path', currentPath);
          
          try {
            const res = await fetch('/api/vps/upload', { method: 'POST', body: formData });
            if (!res.ok) throw new Error(await res.text());
            textEl.textContent = 'Upload complete!';
            setTimeout(() => progressEl.classList.add('hidden'), 2000);
            refreshFiles();
          } catch (err) {
            textEl.textContent = `Upload failed: ${err.message}`;
            setTimeout(() => progressEl.classList.add('hidden'), 3000);
          }
        }
        
        let previewPath = null;
        
        async function previewFile(path, name, mime) {
          previewPath = path;
          const modal = document.getElementById('preview-modal');
          const titleEl = document.getElementById('preview-title');
          const contentEl = document.getElementById('preview-content');
          
          titleEl.textContent = name;
          contentEl.innerHTML = '<div class="text-center text-zinc-500">Loading...</div>';
          modal.classList.remove('hidden');
          modal.classList.add('flex');
          
          try {
            const res = await fetch(`/api/vps/file?path=${encodeURIComponent(path)}`);
            if (!res.ok) throw new Error('Failed to load file');
            
            if (mime.startsWith('image/')) {
              const blob = await res.blob();
              const url = URL.createObjectURL(blob);
              contentEl.innerHTML = `<img src="${url}" class="max-w-full max-h-[60vh] mx-auto rounded-lg" />`;
            } else if (mime.startsWith('text/') || mime.includes('json') || mime.includes('javascript')) {
              const text = await res.text();
              contentEl.innerHTML = `<pre class="text-sm text-zinc-300 overflow-auto"><code>${escapeHtml(text)}</code></pre>`;
            } else if (mime.startsWith('audio/')) {
              const blob = await res.blob();
              const url = URL.createObjectURL(blob);
              contentEl.innerHTML = `<audio controls class="w-full" src="${url}"></audio>`;
            } else {
              contentEl.innerHTML = `<div class="text-center text-zinc-500">Preview not available for this file type.<br>Click Download to view.</div>`;
            }
          } catch (err) {
            contentEl.innerHTML = `<div class="text-center text-red-400">Error: ${err.message}</div>`;
          }
        }
        
        function closePreview() {
          const modal = document.getElementById('preview-modal');
          modal.classList.add('hidden');
          modal.classList.remove('flex');
          previewPath = null;
        }
        
        function downloadCurrentFile() {
          if (previewPath) downloadFile(previewPath);
        }
        
        function downloadFile(path) {
          window.open(`/api/vps/file?path=${encodeURIComponent(path)}`, '_blank');
        }
        
        function escapeHtml(text) {
          const div = document.createElement('div');
          div.textContent = text;
          return div.innerHTML;
        }
        
        // Close modal on backdrop click
        document.getElementById('preview-modal').addEventListener('click', (e) => {
          if (e.target.id === 'preview-modal') closePreview();
        });
      </script>
    </body>
    </html>
    """
    return HTMLResponse(html)

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
