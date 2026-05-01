"""Periodic remote-to-local sync for files uploaded to storage.1ink.us.

Watches configured remote directories on storage.1ink.us and downloads
new/changed files to the local filesystem so projectM and other consumers
always have the latest content.
"""

import asyncio
import logging
from pathlib import Path

from .config import settings
from .ftp_client import StorageFTPClient

logger = logging.getLogger(__name__)

# ── Sync configuration ─────────────────────────────────────────────────────────
# Map: remote_rel_dir -> (local_dir, extensions, remove_stale)
SYNC_MAP: dict[str, tuple[str, tuple[str, ...], bool]] = {
    # Songs / audio
    "flac_songs": ("audio/music", (".flac", ".mp3", ".wav", ".ogg"), False),
    "weeks_songs": ("weeks_songs", (".flac", ".mp3", ".wav", ".ogg"), False),
    # Textures
    "textures": ("textures", (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tga"), False),
    "weeks_textures": ("weeks_textures", (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tga"), False),
    "custom_textures": ("custom_textures", (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tga"), False),
    # Presets
    "custom_milk": ("custom_milk", (".milk",), False),
}

# How often to poll the remote server (seconds)
SYNC_INTERVAL_SECONDS = 300  # 5 minutes


async def run_remote_sync() -> dict[str, dict]:
    """Run one sync pass for all configured directories."""
    results: dict[str, dict] = {}
    base = Path(settings.files_dir)
    client = StorageFTPClient()

    for remote_dir, (local_rel, extensions, remove_stale) in SYNC_MAP.items():
        local_dir = base / local_rel
        try:
            # Run blocking sync in thread pool
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: client.sync_dir_from_remote(
                    remote_dir,
                    local_dir,
                    extensions=extensions,
                    remove_stale=remove_stale,
                )
            )
            results[remote_dir] = result
        except Exception as exc:
            logger.error("Sync failed for %s: %s", remote_dir, exc)
            results[remote_dir] = {"error": str(exc)}

    return results


async def remote_sync_loop():
    """Background loop that periodically syncs remote directories."""
    logger.info("Starting remote sync loop (interval=%ds)", SYNC_INTERVAL_SECONDS)

    # Run immediately on startup
    try:
        results = await run_remote_sync()
        total_downloaded = sum(r.get("downloaded", 0) for r in results.values())
        logger.info("Initial remote sync complete: %d files downloaded", total_downloaded)
    except Exception as exc:
        logger.error("Initial remote sync failed: %s", exc)

    while True:
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)
        try:
            results = await run_remote_sync()
            total_downloaded = sum(r.get("downloaded", 0) for r in results.values())
            if total_downloaded > 0:
                logger.info("Periodic remote sync: %d files downloaded", total_downloaded)
        except Exception as exc:
            logger.error("Periodic remote sync failed: %s", exc)


def start_remote_sync() -> asyncio.Task | None:
    """Start the background sync loop as an asyncio task."""
    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(remote_sync_loop())
        logger.info("Remote sync task created")
        return task
    except RuntimeError:
        logger.warning("No running event loop; remote sync not started")
        return None
