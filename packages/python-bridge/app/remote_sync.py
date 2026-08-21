"""Periodic sync between Contabo local files and storage.1ink.us.

Pulls remote drops into local dirs, and pushes the FLAC library to the
DreamHost fast-mirror path that flac_player prefers:
  local audio/music  ->  remote files/audio/music
  https://storage.1ink.us/files/audio/music/<file>
"""

import asyncio
import logging
from pathlib import Path

import os

from .config import settings
from .ftp_client import StorageFTPClient

logger = logging.getLogger(__name__)

# ── Sync configuration ─────────────────────────────────────────────────────────
# Map: remote_rel_dir -> (local_dir, extensions, remove_stale)
SYNC_MAP: dict[str, tuple[str, tuple[str, ...], bool]] = {
    # Songs / audio (legacy remote drop folder)
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

# Contabo -> DreamHost push mirrors (local_rel, remote_rel, extensions)
PUSH_MAP: list[tuple[str, str, tuple[str, ...]]] = [
    # flac_player rewrites storage.noahcohn.com -> storage.1ink.us keeping /files/...
    ("audio/music", "files/audio/music", (".flac", ".mp3", ".wav", ".ogg")),
]

# How often to poll the remote server (seconds)
SYNC_INTERVAL_SECONDS = 300  # 5 minutes


async def run_remote_sync() -> dict[str, dict]:
    """Run one sync pass for all configured directories."""
    results: dict[str, dict] = {}
    base = Path(settings.files_dir)
    client = StorageFTPClient()
    loop = asyncio.get_running_loop()

    for remote_dir, (local_rel, extensions, remove_stale) in SYNC_MAP.items():
        local_dir = base / local_rel
        try:
            # Capture loop vars via default args to avoid late-binding bugs.
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
            results[f"pull:{remote_dir}"] = result
        except Exception as exc:
            logger.error("Sync failed for %s: %s", remote_dir, exc)
            results[f"pull:{remote_dir}"] = {"error": str(exc)}

    for local_rel, remote_rel, extensions in PUSH_MAP:
        local_dir = base / local_rel
        try:
            result = await loop.run_in_executor(
                None,
                lambda ld=local_dir, rd=remote_rel, ext=extensions: (
                    client.sync_dir_to_remote(ld, rd, extensions=ext)
                ),
            )
            results[f"push:{remote_rel}"] = result
        except Exception as exc:
            logger.error("Push failed for %s: %s", remote_rel, exc)
            results[f"push:{remote_rel}"] = {"error": str(exc)}

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
            total_downloaded = sum(r.get("downloaded", 0) for r in results.values() if isinstance(r, dict))
            total_uploaded = sum(r.get("uploaded", 0) for r in results.values() if isinstance(r, dict))
            if total_downloaded > 0 or total_uploaded > 0:
                logger.info(
                    "Periodic remote sync: %d downloaded, %d uploaded",
                    total_downloaded,
                    total_uploaded,
                )
            else:
                logger.debug("Periodic remote sync complete (no new files)")
        except Exception as exc:
            logger.error("Periodic remote sync failed: %s", exc)
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)


def start_remote_sync() -> asyncio.Task | None:
    """Start the background sync loop as an asyncio task."""
    enabled = os.environ.get("REMOTE_SYNC_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
    if not enabled:
        logger.warning("Remote sync disabled via REMOTE_SYNC_ENABLED")
        return None
    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(remote_sync_loop())
        logger.info("Remote sync task created")
        return task
    except RuntimeError:
        logger.warning("No running event loop; remote sync not started")
        return None
