#!/usr/bin/env python3
import sys
import subprocess
import json
import time
from pathlib import Path

sys.path.insert(0, 'packages/python-bridge')
from pydub import AudioSegment
from pydub.exceptions import CouldntDecodeError
from app.ftp_client import StorageFTPClient

client = StorageFTPClient()
files_dir = Path('/home/ftpbridge/files')

DIR_MAP = {
    files_dir / 'audio' / 'music': 'flac_songs',
    files_dir / 'weeks_songs': 'weeks_songs',
}


def reconvert_file(src: Path) -> bool:
    try:
        audio = AudioSegment.from_file(str(src))
    except Exception as e:
        print(f'  ERROR reading {src.name}: {e}', flush=True)
        return False

    audio = audio.set_frame_rate(44100)
    audio = audio.set_channels(2)
    audio = audio.set_sample_width(3)

    tmp = src.with_suffix('.tmp.flac')
    try:
        audio.export(tmp, format='flac', parameters=['-compression_level', '8'])
        src.unlink()
        tmp.rename(src)
        return True
    except Exception as e:
        print(f'  ERROR exporting {src.name}: {e}', flush=True)
        if tmp.exists():
            tmp.unlink()
        return False


def upload_file(src: Path, remote_dir: str) -> bool:
    remote_rel = f'{remote_dir}/{src.name}'
    try:
        success = client.upload_bytes(src.read_bytes(), remote_rel)
        return success
    except Exception as e:
        print(f'  ERROR uploading {src.name}: {e}', flush=True)
        return False


def main():
    total = 0
    reconverted = 0
    uploaded = 0

    for local_dir, remote_dir in DIR_MAP.items():
        if not local_dir.exists():
            continue
        for src in sorted(local_dir.glob('*.flac')):
            total += 1
            print(f'[{total}] {src.name}', flush=True)

            try:
                result = subprocess.run(
                    ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_streams', str(src)],
                    capture_output=True, text=True, timeout=5
                )
                info = json.loads(result.stdout)
                stream = info['streams'][0]
                fmt = stream.get('sample_fmt', '')
                raw = stream.get('bits_per_raw_sample', '')
                if fmt == 's32' and raw == '24':
                    print(f'  Already 24-bit/s32, uploading only', flush=True)
                    if upload_file(src, remote_dir):
                        uploaded += 1
                    continue
            except Exception:
                pass

            if reconvert_file(src):
                reconverted += 1
                if upload_file(src, remote_dir):
                    uploaded += 1
            time.sleep(1)

    print(f'\nDone. Total: {total}, Reconverted: {reconverted}, Uploaded: {uploaded}', flush=True)


if __name__ == '__main__':
    main()
