"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .logger import get_logger
from .sync import run_poll_loop
from .webhooks import router as webhook_router

settings = get_settings()
log = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("FTP Bridge (Python) starting up – env=%s", settings.app_env)
    task = asyncio.create_task(run_poll_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    log.info("FTP Bridge (Python) shut down")


app = FastAPI(
    title="FTP Bridge – Python",
    description="FastAPI webhook receiver and FTP sync bridge.",
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────────────
origins = settings.cors_origins_list
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(webhook_router)


# ── Health / root ─────────────────────────────────────────────────────────────
@app.get("/health", tags=["ops"])
async def health():
    return {"status": "ok", "service": "python-bridge"}


@app.get("/", tags=["ops"])
async def root():
    return {"message": "FTP Bridge – Python is running"}
