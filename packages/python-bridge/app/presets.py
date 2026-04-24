"""Preset index management for Project-M milkdrop presets.

Fetches directory listings from glsl.1ink.us, parses .milk filenames,
and maintains a cached in-memory + on-disk index.
"""

import html
import json
import logging
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

PRESET_BASE_URL = "https://glsl.1ink.us"
PRESET_DIRS = ["milk", "milkLRG", "milkMED", "milkSML", "custom_milk"]

# In-memory cache: dir -> list of filenames
_preset_index: Dict[str, List[str]] = {}
_last_scan: Optional[datetime] = None


def _get_index_file() -> Path:
    """Path where the persisted index is stored."""
    from .config import settings

    base = Path(settings.files_dir)
    index_dir = base / ".indexes"
    index_dir.mkdir(parents=True, exist_ok=True)
    return index_dir / "presets.json"


def _parse_dir_listing(html_text: str) -> List[str]:
    """Parse nginx autoindex HTML and return .milk filenames."""
    filenames = []
    # Match <a href="..."> tags, extract href value
    for match in re.finditer(r'<a href="([^"]+)">', html_text):
        href = match.group(1)
        # Skip parent directory, query params, and non-.milk files
        if href in ("?C=N;O=D", "?C=M;O=A", "?C=S;O=A", "?C=D;O=A", "/"):
            continue
        if href.endswith("/"):
            continue
        decoded = httpx.URL(href).path
        name = decoded.split("/")[-1]
        name = html.unescape(name)
        if name.lower().endswith(".milk"):
            filenames.append(name)
    return filenames


async def scan_presets() -> Dict[str, int]:
    """Fetch directory listings and rebuild the cached index.

    Returns a dict of dir -> file count.
    """
    global _preset_index, _last_scan

    new_index: Dict[str, List[str]] = {}
    counts: Dict[str, int] = {}

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        for d in PRESET_DIRS:
            url = f"{PRESET_BASE_URL}/{d}/"
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                filenames = _parse_dir_listing(resp.text)
                new_index[d] = filenames
                counts[d] = len(filenames)
                logger.info("Scanned %s: %d presets", d, len(filenames))
            except Exception as exc:
                logger.error("Failed to scan %s: %s", url, exc)
                # Keep existing data if available, otherwise empty list
                new_index[d] = _preset_index.get(d, [])
                counts[d] = len(new_index[d])

    _preset_index = new_index
    _last_scan = datetime.now(timezone.utc)
    _persist_index()
    return counts


def _persist_index() -> None:
    """Write the current in-memory index to disk."""
    data = {
        "dirs": _preset_index,
        "last_scan": _last_scan.isoformat() if _last_scan else None,
    }
    try:
        with open(_get_index_file(), "w") as f:
            json.dump(data, f, indent=2)
    except Exception as exc:
        logger.error("Failed to persist preset index: %s", exc)


def load_index() -> None:
    """Load persisted index from disk into memory."""
    global _preset_index, _last_scan

    idx_file = _get_index_file()
    if not idx_file.exists():
        return
    try:
        with open(idx_file, "r") as f:
            data = json.load(f)
        _preset_index = data.get("dirs", {})
        last_scan_str = data.get("last_scan")
        if last_scan_str:
            _last_scan = datetime.fromisoformat(last_scan_str)
        logger.info(
            "Loaded preset index from disk: %s",
            {k: len(v) for k, v in _preset_index.items()},
        )
    except Exception as exc:
        logger.error("Failed to load preset index: %s", exc)


def get_random_preset(dir_name: Optional[str] = None) -> Optional[Dict[str, str]]:
    """Return a random preset from the cached index.

    Args:
        dir_name: Specific directory (milk, milkLRG, etc.) or "any".

    Returns:
        Dict with keys: dir, filename, url.
        None if no index is available.
    """
    if not _preset_index:
        return None

    if dir_name and dir_name != "any":
        candidates = _preset_index.get(dir_name, [])
        chosen_dir = dir_name
    else:
        # Pick a random dir that has entries
        available = [(d, f) for d, f in _preset_index.items() if f]
        if not available:
            return None
        chosen_dir, candidates = random.choice(available)

    if not candidates:
        return None

    filename = random.choice(candidates)
    return {
        "dir": chosen_dir,
        "filename": filename,
        "url": f"{PRESET_BASE_URL}/{chosen_dir}/{quote(filename)}",
    }


def get_index_stats() -> Dict:
    """Return current index statistics."""
    return {
        "dirs": {d: len(f) for d, f in _preset_index.items()},
        "last_scan": _last_scan.isoformat() if _last_scan else None,
        "total": sum(len(f) for f in _preset_index.values()),
    }
