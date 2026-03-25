#!/usr/bin/env python3
"""
poll_api.py – Standalone script to poll an external REST API and push
records as JSON-lines files to FTP / local volume.

Usage:
    python scripts/poll_api.py [--once]

Environment variables read from .env (or exported):
    EXTERNAL_API_URL, EXTERNAL_API_KEY, POLL_INTERVAL_SECONDS,
    FTP_HOST, FTP_PORT, FTP_USER, FTP_PASS, FTP_UPLOAD_DIR, FTP_TLS,
    FILES_DIR, LOG_LEVEL, LOG_FILE
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running from repo root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent / "packages"))

from shared.config.config import load_config
from shared.logger.logger import get_logger

config = load_config()
log = get_logger("poll_api", log_file=config.get("LOG_FILE"), level=config.get("LOG_LEVEL", "INFO"))


async def poll_once() -> None:
    import httpx

    url = config.get("EXTERNAL_API_URL", "")
    if not url:
        log.warning("EXTERNAL_API_URL is not set – nothing to poll")
        return

    headers = {}
    api_key = config.get("EXTERNAL_API_KEY", "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    log.info("Polling %s", url)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        records = resp.json()

    if not isinstance(records, list):
        records = [records]

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    filename = f"sync/poll_{ts}.jsonl"
    lines = "\n".join(json.dumps(r) for r in records)
    data = lines.encode()

    # Save locally
    files_dir = Path(config.get("FILES_DIR", "/data/files"))
    local_path = files_dir / filename
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(data)
    log.info("Saved %d record(s) → %s", len(records), local_path)

    # Push to FTP
    try:
        import ftplib
        import io
        from pathlib import PurePosixPath

        ftp_tls = config.get("FTP_TLS", "false").lower() == "true"
        ftp: ftplib.FTP
        if ftp_tls:
            import ssl
            ctx = ssl.create_default_context()
            ftp = ftplib.FTP_TLS(context=ctx)
        else:
            ftp = ftplib.FTP()

        ftp.connect(config["FTP_HOST"], int(config.get("FTP_PORT", "21")), timeout=15)
        ftp.login(config["FTP_USER"], config["FTP_PASS"])

        if ftp_tls and isinstance(ftp, ftplib.FTP_TLS):
            ftp.prot_p()

        base = PurePosixPath(config.get("FTP_UPLOAD_DIR", "/home/ftpbridge/files"))
        remote = base / filename

        # Create remote dirs
        parts = remote.parent.parts
        for i in range(1, len(parts) + 1):
            d = str(PurePosixPath(*parts[:i]))
            try:
                ftp.mkd(d)
            except ftplib.error_perm:
                pass

        ftp.storbinary(f"STOR {remote}", io.BytesIO(data))
        ftp.quit()
        log.info("FTP upload OK → %s", remote)
    except Exception as exc:
        log.warning("FTP upload failed (data saved locally): %s", exc)


async def main(once: bool) -> None:
    interval = int(config.get("POLL_INTERVAL_SECONDS", "60"))
    if once:
        await poll_once()
    else:
        log.info("Starting poll loop (interval=%ds)", interval)
        while True:
            try:
                await poll_once()
            except Exception as exc:
                log.error("Poll cycle error: %s", exc)
            await asyncio.sleep(interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Poll external API and push to FTP")
    parser.add_argument("--once", action="store_true", help="Run a single poll and exit")
    args = parser.parse_args()
    asyncio.run(main(args.once))
