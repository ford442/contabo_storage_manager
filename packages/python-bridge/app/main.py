from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
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

# CORS (adjust as needed for your apps)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Change to specific domains in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(webhook_router)
app.include_router(files_router)   # ← Static files router
app.include_router(api_router)     # ← API endpoints for shaders, images, ratings

@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "contabo-storage-manager"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
