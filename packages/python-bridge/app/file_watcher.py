"""File watcher for VPS storage - monitors directories and updates API."""

import asyncio
import logging
from pathlib import Path
from typing import Set, Callable, Optional
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileCreatedEvent, FileMovedEvent

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


class VPSFileWatcher:
    """Watch VPS directories for new files."""
    
    def __init__(
        self,
        files_dir: str,
        on_video: Optional[Callable[[Path], None]] = None,
        on_image: Optional[Callable[[Path], None]] = None,
        on_audio: Optional[Callable[[Path], None]] = None
    ):
        self.files_dir = Path(files_dir)
        self.on_video = on_video
        self.on_image = on_image
        self.on_audio = on_audio
        
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
        
        # Watch audio directory
        audio_dirs = [
            self.files_dir / "audio" / "flac",
            self.files_dir / "audio" / "wav",
        ]
        if self.on_audio:
            for audio_dir in audio_dirs:
                if audio_dir.exists():
                    handler = FileWatcherHandler(
                        self.on_audio,
                        extensions={'.flac', '.wav', '.mp3', '.ogg'}
                    )
                    observer = Observer()
                    observer.schedule(handler, str(audio_dir), recursive=False)
                    observer.start()
                    self.observers.append(observer)
                    logger.info(f"Watching audio directory: {audio_dir}")
    
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
            'audio': []
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
        ]
        for audio_dir in audio_dirs:
            if audio_dir.exists():
                for ext in ['.flac', '.wav', '.mp3', '.ogg']:
                    results['audio'].extend(audio_dir.glob(f"*{ext}"))
        
        return {
            'videos': len(results['videos']),
            'images': len(results['images']),
            'audio': len(results['audio']),
            'video_files': [str(p) for p in results['videos']],
            'image_files': [str(p) for p in results['images']],
            'audio_files': [str(p) for p in results['audio']],
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
    """Start the file watcher."""
    def on_video(path: Path):
        logger.info(f"New video: {path}")
        # Could trigger API update here
    
    def on_image(path: Path):
        logger.info(f"New image: {path}")
        # Could trigger API update here
    
    def on_audio(path: Path):
        logger.info(f"New audio: {path}")
        # Could trigger API update here
    
    watcher = VPSFileWatcher(
        files_dir,
        on_video=on_video,
        on_image=on_image,
        on_audio=on_audio
    )
    watcher.start()
    
    # Log initial scan
    scan = watcher.scan_existing()
    logger.info(f"Initial scan: {scan['videos']} videos, {scan['images']} images, {scan['audio']} audio files")
    
    return watcher
