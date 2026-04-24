#!/usr/bin/env python3
"""Sync MOD files from storage.1ink.us/mods/ to local files/mods/."""

from __future__ import annotations

import html
import logging
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urljoin

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent / "packages"))
from shared.config.config import load_config
from shared.logger.logger import get_logger

config = load_config()
log = get_logger("sync_mods", level=config.get("LOG_LEVEL", "INFO"))

SOURCE_BASE = "https://storage.1ink.us/mods/"
FILES_DIR = Path(config.get("FILES_DIR", "/home/ftpbridge/files"))
DEST_DIR = FILES_DIR / "mods"
DEST_DIR.mkdir(parents=True, exist_ok=True)

MOD_EXTENSIONS = frozenset({
    ".mod", ".xm", ".s3m", ".it", ".mptm", ".stm", ".669", ".amf", ".ams",
    ".dbm", ".dmf", ".dsm", ".far", ".gdm", ".j2b", ".mdl", ".med", ".mtm",
    ".okt", ".psm", ".ptm", ".ult", ".umx", ".mt2", ".mo3",
})


def _parse_dir_listing(text: str) -> list[tuple[str, str]]:
    """Parse nginx autoindex HTML and return (filename, href) pairs."""
    results = []
    for match in re.finditer(r'<a href="([^"]+)">', text):
        href = match.group(1)
        if href in ("?C=N;O=D", "?C=M;O=A", "?C=S;O=A", "?C=D;O=A", "/"):
            continue
        if href.endswith("/"):
            continue
        href = html.unescape(href)
        name = unquote(href).split("/")[-1]
        if name.lower().endswith(tuple(MOD_EXTENSIONS)):
            results.append((name, href))
    return results


async def sync_mods() -> dict:
    log.info("Fetching directory listing from %s", SOURCE_BASE)
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        resp = await client.get(SOURCE_BASE)
        resp.raise_for_status()
        remote_files = _parse_dir_listing(resp.text)
    log.info("Found %d mod files on remote", len(remote_files))

    downloaded = 0
    skipped = 0
    errors = 0

    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        for filename, href in remote_files:
            local_path = DEST_DIR / filename
            url = urljoin(SOURCE_BASE, href)

            # Check if already exists with HEAD request for size
            try:
                head_resp = await client.head(url)
                remote_size = int(head_resp.headers.get("content-length", 0))
            except Exception:
                remote_size = None

            if local_path.exists():
                local_size = local_path.stat().st_size
                if remote_size is not None and local_size == remote_size:
                    skipped += 1
                    continue

            log.info("Downloading %s", filename)
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                local_path.write_bytes(resp.content)
                downloaded += 1
            except Exception as exc:
                log.error("Failed to download %s: %s", filename, exc)
                errors += 1

    # Clean up test files if real files were downloaded
    test_file = DEST_DIR / "test_mod.mod"
    if downloaded > 0 and test_file.exists():
        test_file.unlink()
        log.info("Removed test file %s", test_file)

    log.info("Sync complete: %d downloaded, %d skipped, %d errors", downloaded, skipped, errors)
    return {"downloaded": downloaded, "skipped": skipped, "errors": errors, "total": len(remote_files)}


if __name__ == "__main__":
    import asyncio
    result = asyncio.run(sync_mods())
    print(result)
