from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, JSONResponse
import logging

from .config import settings
from .webhooks import webhook_router, files_router
from .api import api_router

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
app.include_router(files_router)   # ← Static files router
app.include_router(api_router)     # ← API endpoints for shaders, images, ratings

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
