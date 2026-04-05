from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, JSONResponse, HTMLResponse
import logging

from .config import settings
from .webhooks import webhook_router, files_router
from .api import api_router
from .models_router import models_router
from .audio_router import audio_router
from .leaderboard_router import leaderboard_router
from .adventure_router import adventure_router

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Contabo Storage Manager",
    description="Webhook bridge + static file server for image-effects, flac_player, and web_sequencer",
    version="1.0.0",
)

# Enhanced CORS middleware - MUST be added before routers
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
app.include_router(audio_router)        # ← Audio endpoints for music and samples
app.include_router(leaderboard_router)  # ← Leaderboard endpoints for high scores
app.include_router(adventure_router)    # ← Adventure mode endpoints for level progress

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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
