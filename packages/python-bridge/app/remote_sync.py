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
    # Tracker modules (mod-player library)
    "mods": (
        "mods",
        (
            ".mod", ".xm", ".s3m", ".it", ".mptm", ".stm", ".669", ".amf", ".ams",
            ".dbm", ".dmf", ".dsm", ".far", ".gdm", ".j2b", ".mdl", ".med", ".mtm",
            ".okt", ".psm", ".ptm", ".ult", ".umx", ".mt2", ".mo3",
        ),
        False,  # never delete local mods if remote is unreachable
    ),
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
            # Run blocking sync in thread pool.
            # Capture loop vars via default args to avoid late-binding bugs.
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                lambda rd=remote_dir, ld=local_dir, ext=extensions, rs=remove_stale: (
                    client.sync_dir_from_remote(
                        rd,
                        ld,
                        extensions=ext,
                        remove_stale=rs,
                    )
                ),
            )
            results[remote_dir] = result
        except Exception as exc:
            logger.error("Sync failed for %s: %s", remote_dir, exc)
            results[remote_dir] = {"error": str(exc)}

    return results


async def remote_sync_loop():
    """Background loop that periodically syncs remote directories.

    Delays the first pass slightly so uvicorn can finish booting and accept
    health checks before we spend thread-pool time on (possibly dead) remotes.
    """
    logger.info("Starting remote sync loop (interval=%ds)", SYNC_INTERVAL_SECONDS)

    # Let the API come up first; remote host timeouts must not delay /health.
    await asyncio.sleep(15)

    while True:
        try:
            results = await run_remote_sync()
            total_downloaded = sum(r.get("downloaded", 0) for r in results.values())
            if total_downloaded > 0:
                logger.info("Periodic remote sync: %d files downloaded", total_downloaded)
            else:
                logger.debug("Periodic remote sync complete (no new files)")
        except Exception as exc:
            logger.error("Periodic remote sync failed: %s", exc)
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)


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
