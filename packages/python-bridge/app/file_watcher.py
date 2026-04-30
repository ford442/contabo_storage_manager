"""File watcher for VPS storage - monitors directories and updates API."""

import asyncio
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Set, Callable, Optional
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileCreatedEvent, FileMovedEvent

from .config import settings
from .flac_client import register_song_with_flac_player_sync

logger = logging.getLogger(__name__)


class FileWatcherHandler(FileSystemEventHandler):
    """Handle file system events."""
    
    def __init__(
        self,
        on_new_file: Callable[[Path], None],
        extensions: Optional[Set[str]] = None
    ):
        self.on_new_file = on_new_file
        self.extensions = extensions or {'.mp4', '.webm', '.mov', '.avi', '.mkv'}
    
    def on_created(self, event):
        if event.is_directory:
            return
        
        file_path = Path(event.src_path)
        if file_path.suffix.lower() in self.extensions:
            logger.info(f"New file detected: {file_path}")
            self.on_new_file(file_path)
    
    def on_moved(self, event):
        if event.is_directory:
            return
        
        dest_path = Path(event.dest_path)
        if dest_path.suffix.lower() in self.extensions:
            logger.info(f"File moved/renamed: {dest_path}")
            self.on_new_file(dest_path)


def _handle_new_audio(path: Path):
    """Auto-index a new audio file into songs.json and notify the FLAC Player backend."""

    # Give api.py upload endpoint time to write the JSON metadata first.
    # FTP drops don't touch songs.json, so the extra wait is harmless.
    time.sleep(5)

    try:
        # Delayed import to avoid circular imports at module load time
        from .api import _load_songs, _save_songs
    except ImportError as e:
        logger.error(f"Cannot import song helpers: {e}")
        return

    ext = path.suffix.lower()
    allowed_exts = {'.mp3', '.flac', '.wav', '.ogg', '.m4a', '.aac'}

    if ext not in allowed_exts:
        return

    songs = _load_songs()
    filename = path.name

    # Check if already indexed by filename (upload endpoint handles this)
    if any(s.get("filename") == filename for s in songs):
        logger.info(f"Audio file already indexed: {filename}")
        return

    # Try to extract an existing song ID from the filename pattern used by
    # the upload endpoint: {song_id}_{safe_title}.flac
    # This makes the webhook idempotent if the upload endpoint already fired.
    stem = path.stem
    match = re.match(r"^([a-f0-9]{8})_.+$", stem)
    song_id = match.group(1) if match else str(uuid.uuid4())[:8]

    raw_title = stem.split("_", 1)[1].replace("_", " ").replace("-", " ") if "_" in stem and match else stem.replace("_", " ").replace("-", " ")
    title = raw_title.strip() or "Untitled"

    # Absolute public URL for the static file server
    base_url = str(settings.static_base_url).rstrip("/")
    public_url = f"{base_url}/audio/music/{filename}"

    song = {
        "id": song_id,
        "name": f"{title}{ext}",
        "title": title,
        "author": "Unknown",
        "genre": None,
        "rating": None,
        "description": f"Auto-discovered on {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
        "tags": [],
        "duration": None,
        "play_count": 0,
        "last_played": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "filename": filename,
        "url": public_url,
        "size": path.stat().st_size
    }

    songs.append(song)
    _save_songs(songs)
    logger.info(f"Auto-indexed new audio file: {filename} -> {song_id}")

    # Push to external FLAC Player backend so the track appears instantly
    register_song_with_flac_player_sync(
        filename=song["name"],
        public_url=public_url,
        title=title,
        author="Unknown",
        tags=[],
        genre=None,
        duration=None,
        filename_on_storage=filename,
        auto_enrich=True,
        song_id=song_id,
    )


def _handle_new_note(path: Path):
    """Log new note files — notes are served directly from disk by the notes API."""
    logger.info(f"New note detected: {path.name}")


class VPSFileWatcher:
    """Watch VPS directories for new files."""
    
    def __init__(
        self,
        files_dir: str,
        on_video: Optional[Callable[[Path], None]] = None,
        on_image: Optional[Callable[[Path], None]] = None,
        on_audio: Optional[Callable[[Path], None]] = None,
        on_note: Optional[Callable[[Path], None]] = None,
    ):
        self.files_dir = Path(files_dir)
        self.on_video = on_video
        self.on_image = on_image
        self.on_audio = on_audio
        self.on_note = on_note
        
        self.observers: list[Observer] = []
        self._running = False
    
    def start(self):
        """Start watching directories."""
        if self._running:
            return
        
        self._running = True
        
        # Watch video directory
        video_dir = self.files_dir / "videos"
        if video_dir.exists() and self.on_video:
            handler = FileWatcherHandler(
                self.on_video,
                extensions={'.mp4', '.webm', '.mov', '.avi', '.mkv'}
            )
            observer = Observer()
            observer.schedule(handler, str(video_dir), recursive=True)
            observer.start()
            self.observers.append(observer)
            logger.info(f"Watching video directory: {video_dir}")
        
        # Watch image directory
        images_dir = self.files_dir / "image-effects" / "outputs"
        if images_dir.exists() and self.on_image:
            handler = FileWatcherHandler(
                self.on_image,
                extensions={'.png', '.jpg', '.jpeg', '.webp', '.gif'}
            )
            observer = Observer()
            observer.schedule(handler, str(images_dir), recursive=True)
            observer.start()
            self.observers.append(observer)
            logger.info(f"Watching image directory: {images_dir}")
        
        # Watch audio directories (including music/ for flac_player)
        audio_dirs = [
            self.files_dir / "audio" / "flac",
            self.files_dir / "audio" / "wav",
            self.files_dir / "audio" / "music",
        ]
        if self.on_audio:
            for audio_dir in audio_dirs:
                if audio_dir.exists():
                    handler = FileWatcherHandler(
                        self.on_audio,
                        extensions={'.flac', '.wav', '.mp3', '.ogg', '.m4a', '.aac'}
                    )
                    observer = Observer()
                    observer.schedule(handler, str(audio_dir), recursive=False)
                    observer.start()
                    self.observers.append(observer)
                    logger.info(f"Watching audio directory: {audio_dir}")
        
        # Watch notes directory
        notes_dir = self.files_dir / "notes"
        if notes_dir.exists() and self.on_note:
            handler = FileWatcherHandler(
                self.on_note,
                extensions={'.md'}
            )
            observer = Observer()
            observer.schedule(handler, str(notes_dir), recursive=False)
            observer.start()
            self.observers.append(observer)
            logger.info(f"Watching notes directory: {notes_dir}")
    
    def stop(self):
        """Stop watching directories."""
        self._running = False
        for observer in self.observers:
            observer.stop()
            observer.join()
        self.observers.clear()
        logger.info("File watcher stopped")
    
    def scan_existing(self) -> dict:
        """Scan existing files and return counts."""
        results = {
            'videos': [],
            'images': [],
            'audio': [],
            'notes': []
        }
        
        # Scan videos
        video_dir = self.files_dir / "videos"
        if video_dir.exists():
            for ext in ['.mp4', '.webm', '.mov', '.avi', '.mkv']:
                results['videos'].extend(video_dir.glob(f"*{ext}"))
        
        # Scan images
        images_dir = self.files_dir / "image-effects" / "outputs"
        if images_dir.exists():
            for date_dir in images_dir.iterdir():
                if date_dir.is_dir():
                    for ext in ['.png', '.jpg', '.jpeg', '.webp', '.gif']:
                        results['images'].extend(date_dir.glob(f"*{ext}"))
        
        # Scan audio
        audio_dirs = [
            self.files_dir / "audio" / "flac",
            self.files_dir / "audio" / "wav",
            self.files_dir / "audio" / "music",
        ]
        for audio_dir in audio_dirs:
            if audio_dir.exists():
                for ext in ['.flac', '.wav', '.mp3', '.ogg', '.m4a', '.aac']:
                    results['audio'].extend(audio_dir.glob(f"*{ext}"))
        
        # Scan notes
        notes_dir = self.files_dir / "notes"
        if notes_dir.exists():
            results['notes'].extend(notes_dir.glob("*.md"))
        
        return {
            'videos': len(results['videos']),
            'images': len(results['images']),
            'audio': len(results['audio']),
            'notes': len(results['notes']),
            'video_files': [str(p) for p in results['videos']],
            'image_files': [str(p) for p in results['images']],
            'audio_files': [str(p) for p in results['audio']],
            'note_files': [str(p) for p in results['notes']],
        }


# Global watcher instance
_watcher_instance: Optional[VPSFileWatcher] = None


def get_watcher(files_dir: str) -> VPSFileWatcher:
    """Get or create watcher singleton."""
    global _watcher_instance
    if _watcher_instance is None:
        _watcher_instance = VPSFileWatcher(files_dir)
    return _watcher_instance


def start_watching(files_dir: str):
    """Start the file watcher with sensible defaults."""
    def on_video(path: Path):
        logger.info(f"New video: {path}")
    
    def on_image(path: Path):
        logger.info(f"New image: {path}")
    
    def on_audio(path: Path):
        _handle_new_audio(path)
    
    def on_note(path: Path):
        _handle_new_note(path)
    
    watcher = VPSFileWatcher(
        files_dir,
        on_video=on_video,
        on_image=on_image,
        on_audio=on_audio,
        on_note=on_note,
    )
    watcher.start()
    
    # Log initial scan
    scan = watcher.scan_existing()
    logger.info(
        f"Initial scan: {scan['videos']} videos, {scan['images']} images, "
        f"{scan['audio']} audio files, {scan['notes']} notes"
    )
    
    return watcher
