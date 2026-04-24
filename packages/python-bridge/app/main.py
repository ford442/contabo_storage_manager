from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.database import engine
from app.models import Base
from app.api import router as api_router
from app.pachinball_api import pachinball_router
from app.mod_router import mod_router
import uvicorn

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Contabo Storage Manager API",
    description="Manages files, songs, playlists, and shares on Contabo storage.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
app.include_router(pachinball_router)  # ← Pachinball API endpoints
app.include_router(mod_router)  # ← MOD music file endpoints (/api/mods)

@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "contabo-storage-manager"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
