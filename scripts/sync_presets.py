#!/usr/bin/env python3
"""Sync MilkDrop presets from glsl.1ink.us to local files/milk*/ directories."""

from __future__ import annotations

import asyncio
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
log = get_logger("sync_presets", level=config.get("LOG_LEVEL", "INFO"))

SOURCE_BASE = "https://glsl.1ink.us"
FILES_DIR = Path(config.get("FILES_DIR", "/home/ftpbridge/files"))
PRESET_DIRS = ["milk", "milkSML", "milkMED", "milkLRG", "custom_milk"]
CONCURRENCY = 10


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
        if name.lower().endswith(".milk"):
            results.append((name, href))
    return results


async def sync_presets() -> dict:
    total_downloaded = 0
    total_skipped = 0
    total_errors = 0
    total_files = 0

    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def download_one(client: httpx.AsyncClient, dir_name: str, filename: str, href: str, dest_dir: Path) -> tuple[str, int]:
        """Download a single preset. Returns ('downloaded'|'skipped'|'error', size)."""
        local_path = dest_dir / filename
        url = urljoin(f"{SOURCE_BASE}/{dir_name}/", href)

        async with semaphore:
            try:
                head_resp = await client.head(url)
                remote_size = int(head_resp.headers.get("content-length", 0))
            except Exception:
                remote_size = None

            if local_path.exists():
                local_size = local_path.stat().st_size
                if remote_size is not None and local_size == remote_size:
                    return ("skipped", 0)

            try:
                file_resp = await client.get(url)
                file_resp.raise_for_status()
                local_path.write_bytes(file_resp.content)
                return ("downloaded", len(file_resp.content))
            except Exception as exc:
                log.error("Failed to download %s/%s: %s", dir_name, filename, exc)
                return ("error", 0)

    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        for dir_name in PRESET_DIRS:
            source_url = f"{SOURCE_BASE}/{dir_name}/"
            dest_dir = FILES_DIR / dir_name
            dest_dir.mkdir(parents=True, exist_ok=True)

            log.info("Fetching %s ...", source_url)
            try:
                resp = await client.get(source_url)
                resp.raise_for_status()
            except Exception as exc:
                log.error("Failed to list %s: %s", source_url, exc)
                continue

            remote_files = _parse_dir_listing(resp.text)
            total_files += len(remote_files)
            log.info("Found %d presets in %s", len(remote_files), dir_name)

            tasks = [
                download_one(client, dir_name, filename, href, dest_dir)
                for filename, href in remote_files
            ]

            downloaded = 0
            skipped = 0
            errors = 0

            for coro in asyncio.as_completed(tasks):
                status, _ = await coro
                if status == "downloaded":
                    downloaded += 1
                elif status == "skipped":
                    skipped += 1
                elif status == "error":
                    errors += 1

            total_downloaded += downloaded
            total_skipped += skipped
            total_errors += errors
            log.info("%s: %d downloaded, %d skipped, %d errors", dir_name, downloaded, skipped, errors)

    # Clean up test preset if real files were downloaded
    test_file = FILES_DIR / "milk" / "test_preset.milk"
    if total_downloaded > 0 and test_file.exists():
        test_file.unlink()
        log.info("Removed test file %s", test_file)

    log.info(
        "Overall: %d downloaded, %d skipped, %d errors, %d total",
        total_downloaded, total_skipped, total_errors, total_files,
    )
    return {
        "downloaded": total_downloaded,
        "skipped": total_skipped,
        "errors": total_errors,
        "total": total_files,
    }


if __name__ == "__main__":
    result = asyncio.run(sync_presets())
    print(result)
