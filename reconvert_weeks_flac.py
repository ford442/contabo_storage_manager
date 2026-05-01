#!/usr/bin/env python3
"""Reconvert all 'weeks' tagged FLAC files to 16-bit/44.1kHz/stereo and re-upload to remote storage."""

import json
import sys
import os
from pathlib import Path

# Ensure we're using the project venv
VENV_PYTHON = Path(__file__).parent / "venv" / "bin" / "python3"
if sys.executable != str(VENV_PYTHON) and VENV_PYTHON.exists():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), __file__])

from pydub import AudioSegment
from pydub.exceptions import CouldntDecodeError

# Add the app package to path
sys.path.insert(0, str(Path(__file__).parent / "packages" / "python-bridge"))

from app.config import settings
from app.ftp_client import ftp_client

FILES_DIR = Path(settings.files_dir)
SONGS_JSON = FILES_DIR / "songs.json"
MUSIC_DIR = FILES_DIR / "audio" / "music"
REMOTE_DIR = settings.external_flac_dir  # typically "flac_songs"


def load_weeks_songs():
    with open(SONGS_JSON) as f:
        data = json.load(f)
    songs = data.get("songs", [])
    weeks_songs = []
    for song in songs:
        tags = song.get("tags", []) or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        if "weeks" in [t.lower() for t in tags]:
            weeks_songs.append(song)
    return weeks_songs


def reconvert_flac(src: Path, dest: Path) -> bool:
    try:
        audio = AudioSegment.from_file(str(src))
    except (CouldntDecodeError, FileNotFoundError) as e:
        print(f"  ERROR: Could not decode {src.name}: {e}")
        return False

    audio = audio.set_frame_rate(44100)
    audio = audio.set_channels(2)
    audio = audio.set_sample_width(2)  # 16-bit
    audio.export(dest, format="flac", parameters=["-compression_level", "8"])
    return True


def main():
    weeks_songs = load_weeks_songs()
    print(f"Found {len(weeks_songs)} songs with 'weeks' tag\n")

    for song in weeks_songs:
        filename = song.get("filename")
        if not filename:
            continue

        src = MUSIC_DIR / filename
        if not src.exists():
            print(f"SKIP: {filename} not found locally")
            continue

        print(f"Processing: {filename}")

        # Reconvert in-place (overwrite local copy)
        temp_dest = src.with_suffix(".tmp.flac")
        if not reconvert_flac(src, temp_dest):
            if temp_dest.exists():
                temp_dest.unlink()
            continue

        # Replace original with reconverted file
        src.unlink()
        temp_dest.rename(src)
        new_size = src.stat().st_size
        print(f"  -> Reconverted to 16/44.1/stereo ({new_size} bytes)")

        # Re-upload to remote storage
        remote_rel = f"{REMOTE_DIR}/{filename}"
        with open(src, "rb") as f:
            file_bytes = f.read()

        result = ftp_client.upload(file_bytes, remote_rel)
        if result:
            print(f"  -> Uploaded to storage.1ink.us/{remote_rel}")
        else:
            print(f"  -> FAILED to upload to storage.1ink.us/{remote_rel}")

    print("\nDone.")


if __name__ == "__main__":
    main()
