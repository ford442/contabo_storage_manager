#!/usr/bin/env python3
"""Reconvert FLAC files to 16-bit/44.1kHz/stereo and upload to remote storage."""

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
from app.ftp_client import upload_bytes

FILES_DIR = Path(settings.files_dir)
SONGS_JSON = FILES_DIR / "songs.json"
MUSIC_DIR = FILES_DIR / "audio" / "music"
REMOTE_DIR = settings.external_flac_dir  # typically "flac_songs"

# Files listed at noahcohn.com/weeks_songs/
WEEKS_SONGS_FILENAMES = [
    "007423e7_ÉchosdelAsphalte.flac",
    "0107d857_LeBoutonScan.flac",
    "193a862f_WildChild.flac",
    "26656f91_LeSommeildesLumières.flac",
    "52a333a6_weeksonfire2_1.flac",
    "5f3eaa9a_weeksonfire2_2.flac",
    "66b5d85a_LesPharesdeMinuit.flac",
    "714c6fd7_MystèreAnimalfrench.flac",
    "7ef11011_LaRouteenStéréo.flac",
    "814d9458_BoulevarddesIllusions.flac",
    "9558e828_AtmosphericSteelDrift2.flac",
    "a2c4a2da_sambanightfrench.flac",
    "a5311b47_LeDernierMétro.flac",
    "aec71e41_MystèreAnimalenglishver2.flac",
    "b52e8be2_LesOndesCourtes.flac",
    "b8507966_1930sParisiancabaret.flac",
    "bc0766af_sambanightenglish.flac",
    "becde1b2_AtmosphericSteelDrift2_1.flac",
    "c9cd0561_MystèreAnimalfrenchver2.flac",
    "db41f130_MystèreAnimalenglish.flac",
    "e295901e_weeksonfire2.flac",
]


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


def process_file(filename: str) -> bool:
    src = MUSIC_DIR / filename
    if not src.exists():
        print(f"SKIP: {filename} not found locally")
        return False

    print(f"Processing: {filename}")

    # Reconvert in-place (overwrite local copy)
    temp_dest = src.with_suffix(".tmp.flac")
    if not reconvert_flac(src, temp_dest):
        if temp_dest.exists():
            temp_dest.unlink()
        return False

    src.unlink()
    temp_dest.rename(src)
    new_size = src.stat().st_size
    print(f"  -> Reconverted to 16/44.1/stereo ({new_size} bytes)")

    # Upload to remote storage using sync helper
    remote_rel = f"{REMOTE_DIR}/{filename}"
    with open(src, "rb") as f:
        file_bytes = f.read()

    success = upload_bytes(file_bytes, remote_rel)
    if success:
        print(f"  -> Uploaded to storage.1ink.us/{remote_rel}")
    else:
        print(f"  -> FAILED to upload to storage.1ink.us/{remote_rel}")

    return success


def main():
    # Also include any songs with 'weeks' tag from songs.json
    weeks_tagged = set(WEEKS_SONGS_FILENAMES)
    try:
        with open(SONGS_JSON) as f:
            data = json.load(f)
        for song in data.get("songs", []):
            tags = song.get("tags", []) or []
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",") if t.strip()]
            if "weeks" in [t.lower() for t in tags]:
                weeks_tagged.add(song.get("filename", ""))
    except Exception:
        pass

    # Filter to existing files
    to_process = [f for f in sorted(weeks_tagged) if f and (MUSIC_DIR / f).exists()]
    print(f"Found {len(to_process)} files to process\n")

    success_count = 0
    for filename in to_process:
        if process_file(filename):
            success_count += 1

    print(f"\nDone. {success_count}/{len(to_process)} uploaded successfully.")


if __name__ == "__main__":
    main()
