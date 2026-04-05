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
async def home():
    """Beautiful visual dashboard for storage.noahcohn.com"""
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>storage.noahcohn.com</title>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');
            body {
                margin: 0;
                font-family: 'Inter', system-ui, sans-serif;
                background: #0a0a0a;
                color: #eee;
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
            }
            .dashboard {
                max-width: 960px;
                padding: 40px;
                text-align: center;
            }
            h1 {
                font-size: 3.2rem;
                margin: 0 0 8px;
                background: linear-gradient(90deg, #00ddff, #ff00aa);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
            }
            .tagline { font-size: 1.3rem; color: #888; margin-bottom: 40px; }
            .stats {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
                gap: 20px;
                margin: 40px 0;
            }
            .stat-card {
                background: #111;
                padding: 24px;
                border-radius: 16px;
                border: 1px solid #222;
            }
            .stat-number { font-size: 2.4rem; font-weight: 600; color: #00ddff; }
            .footer {
                margin-top: 60px;
                color: #555;
                font-size: 0.95rem;
            }
            a { color: #00ddff; text-decoration: none; }
        </style>
    </head>
    <body>
        <div class="dashboard">
            <h1>storage.noahcohn.com</h1>
            <p class="tagline">Google Cloud Storage • FastAPI • Shader &amp; Media Manager</p>
            
            <div class="stats" id="stats">
                <!-- Populated by JS from /api/health -->
            </div>

            <p>
                <a href="/docs" style="margin-right: 30px;">📖 Swagger API Docs</a>
                <a href="/api/shaders">🎨 Browse Shaders</a>
            </p>

            <div class="footer">
                Powered by your Contabo VPS • 
                <a href="https://github.com/ford442/contabo_storage_manager" target="_blank">GitHub</a>
            </div>
        </div>

        <script>
            fetch('/api/health')
                .then(r => r.json())
                .then(data => {
                    const container = document.getElementById('stats');
                    let html = '';
                    for (const [key, val] of Object.entries(data.storage || {})) {
                        if (val.count !== undefined) {
                            html += `
                                <div class="stat-card">
                                    <div style="text-transform:uppercase; font-size:0.9rem; color:#666;">${key}</div>
                                    <div class="stat-number">${val.count}</div>
                                </div>`;
                        }
                    }
                    container.innerHTML = html || '<p style="color:#666;">No storage stats available yet</p>';
                });
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
