"""Background sync task: polls an external API and pushes records to FTP."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import httpx

from .config import get_settings
from .ftp_client import upload_bytes
from .logger import get_logger

log = get_logger("sync")
settings = get_settings()


async def fetch_and_sync() -> None:
    """Single poll cycle: fetch from external API, write JSON lines to FTP."""
    if not settings.external_api_url:
        log.debug("EXTERNAL_API_URL not set – skipping poll cycle")
        return

    headers = {}
    if settings.external_api_key:
        headers["Authorization"] = f"Bearer {settings.external_api_key}"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(settings.external_api_url, headers=headers)
            resp.raise_for_status()
            records = resp.json()
    except Exception as exc:
        log.error("Poll failed: %s", exc)
        return

    if not isinstance(records, list):
        records = [records]

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    filename = f"sync/poll_{ts}.jsonl"
    lines = "\n".join(json.dumps(r) for r in records)

    try:
        upload_bytes(lines.encode(), filename)
        log.info("Synced %d record(s) → %s", len(records), filename)
    except Exception as exc:
        log.error("FTP upload failed during sync: %s", exc)


async def run_poll_loop() -> None:
    """Run fetch_and_sync every POLL_INTERVAL_SECONDS indefinitely."""
    log.info("Starting poll loop (interval=%ds)", settings.poll_interval_seconds)
    while True:
        await fetch_and_sync()
        await asyncio.sleep(settings.poll_interval_seconds)
