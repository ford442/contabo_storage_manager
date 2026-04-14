"""Main entry point for GCS-enabled storage manager."""

import sys
sys.path.insert(0, '/root/contabo_storage_manager/packages/python-bridge')

# DEPRECATED: Use app.main:app for the unified Storage Manager API.
# api_full.py does not include /api/songs/tags, /api/songs/stats, or /api/music/{id}
# required by flac_player. Switching to app.main:app.
from app.main import app
from app.file_watcher import start_watching
from app.config import settings

if __name__ == "__main__":
    import uvicorn
    import logging

    logger = logging.getLogger(__name__)
    
    # Start the local file watcher so files synced from the Google Bucket
    # are automatically detected and indexed (e.g. audio -> songs.json).
    try:
        watcher = start_watching(settings.files_dir)
        logger.info(f"File watcher started for {settings.files_dir}")
    except Exception as e:
        logger.error(f"Failed to start file watcher: {e}")
    
    # Run with a single worker so the background file watcher thread
    # isn't duplicated across forked processes.
    uvicorn.run(app, host="0.0.0.0", port=8000, workers=1)
