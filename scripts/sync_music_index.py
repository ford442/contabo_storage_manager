#!/usr/bin/env python3
"""Sync local audio files with the master songs.json index.

Scans the audio/music directory for supported audio files and ensures each
one has a valid entry in songs.json. Generates default metadata for any
missing files.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

FILES_DIR = os.environ.get("FILES_DIR", "/home/ftpbridge/files")
MUSIC_DIR = Path(FILES_DIR) / "audio" / "music"
SONGS_FILE = Path(FILES_DIR) / "songs.json"

ALLOWED_EXTS = {".flac", ".mp3", ".wav", ".ogg"}


def load_songs() -> list:
    """Load existing songs from songs.json."""
    if not SONGS_FILE.exists():
        return []
    try:
        with open(SONGS_FILE, "r") as f:
            data = json.load(f)
            return data.get("songs", []) if isinstance(data, dict) else data
    except (json.JSONDecodeError, IOError) as e:
        print(f"Warning: failed to load {SONGS_FILE}: {e}")
        return []


def save_songs(songs: list):
    """Save songs back to songs.json."""
    SONGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SONGS_FILE, "w") as f:
        json.dump({"songs": songs}, f, indent=2)


def build_entry(filename: str, size: int) -> dict:
    """Generate a default metadata entry for an audio file."""
    song_id = str(uuid.uuid4())
    raw_title = Path(filename).stem.replace("_", " ").replace("-", " ").strip()
    title = raw_title or "Untitled"
    return {
        "id": song_id,
        "name": filename,
        "title": title,
        "filename": filename,
        "author": "Unknown",
        "genre": None,
        "rating": None,
        "description": "",
        "tags": [],
        "duration": None,
        "play_count": 0,
        "last_played": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "url": f"/api/music/{song_id}",
        "size": size,
    }


def sync():
    """Main sync routine."""
    if not MUSIC_DIR.exists():
        print(f"Music directory not found: {MUSIC_DIR}")
        return

    songs = load_songs()
    existing_filenames = {s.get("filename") for s in songs if s.get("filename")}
    added = 0

    for file_path in sorted(MUSIC_DIR.iterdir()):
        if not file_path.is_file():
            continue
        ext = file_path.suffix.lower()
        if ext not in ALLOWED_EXTS:
            continue

        filename = file_path.name
        if filename in existing_filenames:
            continue

        size = file_path.stat().st_size
        entry = build_entry(filename, size)
        songs.append(entry)
        added += 1
        print(f"  + Added: {filename} -> {entry['id']}")

    if added:
        save_songs(songs)
        print(f"\nSynced {added} new track(s). Total tracks: {len(songs)}")
    else:
        print(f"\nNo new tracks found. Total tracks: {len(songs)}")


if __name__ == "__main__":
    sync()
