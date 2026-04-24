#!/usr/bin/env python3
"""Standalone MOD indexer using openmpt123.

Scans FILES_DIR/mods/ for tracker files, extracts metadata (title, author,
duration) via the native openmpt123 CLI, and writes index.json.

Can be run manually or via cron:
    0 * * * * cd /path/to/project && /usr/bin/python3 scripts/index_mods.py > /dev/null 2>&1
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "packages"))
from shared.config.config import load_config
from shared.logger.logger import get_logger

config = load_config()
log = get_logger("index_mods", level=config.get("LOG_LEVEL", "INFO"))

FILES_DIR = Path(config.get("FILES_DIR", "/home/ftpbridge/files"))
MODS_DIR = FILES_DIR / "mods"
INDEX_FILE = MODS_DIR / "index.json"

MOD_EXTENSIONS = frozenset({
    ".mod", ".xm", ".s3m", ".it", ".mptm", ".stm", ".669", ".amf", ".ams",
    ".dbm", ".dmf", ".dsm", ".far", ".gdm", ".j2b", ".mdl", ".med", ".mtm",
    ".okt", ".psm", ".ptm", ".ult", ".umx", ".mt2", ".mo3",
})


def _parse_duration(value: str) -> float:
    """Parse openmpt123 duration strings like '00:07.680' or '01:02:03.456'."""
    value = value.strip()
    parts = value.split(":")
    try:
        if len(parts) == 2:
            minutes = float(parts[0])
            seconds = float(parts[1])
            return minutes * 60 + seconds
        elif len(parts) == 3:
            hours = float(parts[0])
            minutes = float(parts[1])
            seconds = float(parts[2])
            return hours * 3600 + minutes * 60 + seconds
    except ValueError:
        pass
    return 0.0


def extract_metadata(filepath: Path) -> dict:
    """Run openmpt123 --info and extract Title, Tracker (author), and Duration."""
    result = {"title": "", "author": "", "duration": 0.0}
    try:
        proc = subprocess.run(
            ["openmpt123", "--info", str(filepath)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0 and not proc.stdout:
            log.warning("openmpt123 failed for %s: %s", filepath.name, proc.stderr)
            return result

        for line in proc.stdout.splitlines():
            if ":" not in line:
                continue
            key_part, _, value = line.partition(":")
            key = key_part.strip(". ").lower()
            value = value.strip()

            if key == "title":
                result["title"] = value
            elif key == "tracker":
                result["author"] = value
            elif key == "duration":
                result["duration"] = _parse_duration(value)
    except subprocess.TimeoutExpired:
        log.warning("openmpt123 timed out for %s", filepath.name)
    except FileNotFoundError:
        log.error("openmpt123 not found. Install it with: sudo apt-get install openmpt123")
        raise SystemExit(1)
    except Exception as exc:
        log.warning("Error extracting metadata from %s: %s", filepath.name, exc)

    return result


def load_index() -> dict:
    if INDEX_FILE.exists():
        try:
            with open(INDEX_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Migrate legacy list format to dict keyed by id
            if isinstance(data, list):
                return {item["id"]: item for item in data if "id" in item}
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def save_index(index: dict) -> None:
    MODS_DIR.mkdir(parents=True, exist_ok=True)
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2)


def generate_index():
    MODS_DIR.mkdir(parents=True, exist_ok=True)
    index = load_index()

    scanned = 0
    added = 0
    updated = 0

    now = datetime.now(timezone.utc).isoformat()
    static_base_url = config.get("STATIC_BASE_URL", "https://storage.1ink.us")

    for filepath in MODS_DIR.iterdir():
        if not filepath.is_file():
            continue

        ext = filepath.suffix.lower()
        if ext not in MOD_EXTENSIONS:
            continue

        scanned += 1
        filename = filepath.name
        file_id = Path(filename).stem.lower().replace(" ", "_")
        size = filepath.stat().st_size

        meta = extract_metadata(filepath)

        if file_id not in index:
            index[file_id] = {
                "id": file_id,
                "filename": filename,
                "title": meta["title"] or Path(filename).stem,
                "author": meta["author"],
                "duration": meta["duration"],
                "size": size,
                "tags": [],
                "notes": "",
                "url": f"{static_base_url}/mods/{filename}",
                "added_at": now,
                "updated_at": now,
            }
            added += 1
            log.info("Added: %s -> '%s' by '%s' (%.2fs)", filename, meta["title"] or file_id, meta["author"], meta["duration"])
        else:
            entry = index[file_id]
            entry["size"] = size
            entry["updated_at"] = now
            changed = False

            if meta["title"] and (not entry.get("title") or entry.get("title") == Path(filename).stem):
                entry["title"] = meta["title"]
                changed = True
            if meta["author"] and not entry.get("author"):
                entry["author"] = meta["author"]
                changed = True
            if meta["duration"] > 0 and entry.get("duration", 0.0) == 0.0:
                entry["duration"] = meta["duration"]
                changed = True

            if changed:
                log.info("Updated: %s -> '%s' by '%s' (%.2fs)", filename, entry["title"], entry["author"], entry["duration"])
            updated += 1

    save_index(index)
    log.info("Done: %d scanned, %d added, %d updated, %d total", scanned, added, updated, len(index))


if __name__ == "__main__":
    generate_index()
