#!/usr/bin/env python3
"""Sync music files from GCS bucket to local storage and generate songs.json.

Uses the public GCS JSON API (no credentials required for public buckets).
"""

import json
import logging
import os
import sys
from pathlib import Path

import httpx

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

BUCKET_NAME = os.environ.get("GCP_BUCKET_NAME", "my-sd35-space-images-2025")
FILES_DIR = os.environ.get("FILES_DIR", "/home/ftpbridge/files")
MUSIC_PREFIX = "music/"
LOCAL_MUSIC_DIR = Path(FILES_DIR) / "audio" / "music"
SONGS_FILE = Path(FILES_DIR) / "songs.json"
GCS_BASE_URL = f"https://storage.googleapis.com/{BUCKET_NAME}"
GCS_API_BASE = "https://storage.googleapis.com/storage/v1"


def list_blobs(bucket: str, prefix: str) -> list:
    """List all blobs in a GCS prefix using the public JSON API."""
    blobs = []
    page_token = None
    while True:
        params = {"prefix": prefix, "maxResults": "1000"}
        if page_token:
            params["pageToken"] = page_token

        url = f"{GCS_API_BASE}/b/{bucket}/o"
        resp = httpx.get(url, params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        items = data.get("items", [])
        blobs.extend(items)
        page_token = data.get("nextPageToken")
        if not page_token:
            break

    return blobs


def download_blob(bucket: str, object_name: str, destination: Path):
    """Download a GCS blob to a local path."""
    encoded_name = object_name.replace("/", "%2F")
    url = f"{GCS_API_BASE}/b/{bucket}/o/{encoded_name}?alt=media"
    # Fallback to direct public URL if API URL has issues with spaces
    if " " in object_name or "%20" in object_name:
        url = f"{GCS_BASE_URL}/{object_name}"

    with httpx.stream("GET", url, timeout=120, follow_redirects=True) as response:
        response.raise_for_status()
        with open(destination, "wb") as f:
            for chunk in response.iter_bytes(chunk_size=8192):
                f.write(chunk)


def normalize_songs(raw_songs: list) -> list:
    """Normalize _music.json entries to the format expected by api.py and flac_player."""
    from datetime import datetime, timezone
    normalized = []
    for item in raw_songs:
        if not isinstance(item, dict):
            continue
        song_id = item.get("id") or item.get("song_id")
        filename = item.get("filename") or item.get("name")
        if not song_id or not filename:
            logger.warning(f"Skipping invalid entry: {item}")
            continue

        # Rewrite URL to use the local Storage Manager API
        url = f"/api/music/{song_id}"

        # Normalize tags from genre
        tags = item.get("tags")
        if tags is None and item.get("genre"):
            tags = [g.strip() for g in str(item["genre"]).split(",") if g.strip()]
        if tags is None:
            tags = []

        play_count = item.get("play_count")
        if play_count is None:
            play_count = 0

        created_at = item.get("created_at") or item.get("date")
        if not created_at:
            created_at = datetime.now(timezone.utc).isoformat()

        normalized.append({
            "id": song_id,
            "name": item.get("name") or filename,
            "title": item.get("title") or item.get("name") or Path(filename).stem.replace("_", " ").replace("-", " ").strip() or "Untitled",
            "filename": filename,
            "author": item.get("author") or "Unknown",
            "genre": item.get("genre") or None,
            "rating": item.get("rating") or None,
            "description": item.get("description") or "",
            "tags": tags,
            "duration": item.get("duration") or None,
            "play_count": play_count,
            "last_played": item.get("last_played") or None,
            "created_at": created_at,
            "url": url,
            "size": item.get("size") or 0,
        })
    return normalized


def sync():
    LOCAL_MUSIC_DIR.mkdir(parents=True, exist_ok=True)

    logger.info(f"Listing objects in gs://{BUCKET_NAME}/{MUSIC_PREFIX} ...")
    blobs = list_blobs(BUCKET_NAME, MUSIC_PREFIX)
    logger.info(f"Found {len(blobs)} objects.")

    index_blob = None
    audio_blobs = []
    for blob in blobs:
        name = blob["name"]
        if name == MUSIC_PREFIX:
            continue
        if name.endswith("_music.json"):
            index_blob = blob
        else:
            audio_blobs.append(blob)

    logger.info(f"Audio files: {len(audio_blobs)}, Index file: {'yes' if index_blob else 'no'}")

    # Download audio files
    downloaded = 0
    skipped = 0
    failed = 0
    for blob in audio_blobs:
        filename = Path(blob["name"]).name
        local_path = LOCAL_MUSIC_DIR / filename
        size = int(blob.get("size", 0))
        if local_path.exists() and local_path.stat().st_size == size:
            skipped += 1
            continue
        try:
            logger.info(f"Downloading {filename} ({size} bytes)...")
            download_blob(BUCKET_NAME, blob["name"], local_path)
            downloaded += 1
        except Exception as exc:
            logger.error(f"Failed to download {filename}: {exc}")
            failed += 1

    logger.info(f"Downloaded {downloaded}, skipped {skipped}, failed {failed}.")

    # Build songs.json
    if index_blob:
        logger.info("Downloading _music.json index...")
        index_path = Path("/tmp/_music.json")
        download_blob(BUCKET_NAME, index_blob["name"], index_path)
        with open(index_path) as f:
            index_data = json.load(f)
        songs = normalize_songs(index_data)
    else:
        logger.warning("No _music.json found. Scanning local audio files...")
        from datetime import datetime, timezone
        songs = []
        for ext in [".flac", ".wav", ".mp3", ".ogg", ".m4a", ".aac"]:
            for path in LOCAL_MUSIC_DIR.glob(f"*{ext}"):
                song_id = os.urandom(16).hex()[:8]
                title = path.stem.replace("_", " ").replace("-", " ").strip() or "Untitled"
                songs.append({
                    "id": song_id,
                    "name": path.name,
                    "title": title,
                    "author": "Unknown",
                    "genre": None,
                    "rating": None,
                    "description": "Auto-discovered from GCS sync",
                    "tags": [],
                    "duration": None,
                    "play_count": 0,
                    "last_played": None,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "filename": path.name,
                    "url": f"/api/music/{song_id}",
                    "size": path.stat().st_size,
                })

    SONGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SONGS_FILE, "w") as f:
        json.dump({"songs": songs}, f, indent=2)

    logger.info(f"Wrote {len(songs)} songs to {SONGS_FILE}")


if __name__ == "__main__":
    sync()
