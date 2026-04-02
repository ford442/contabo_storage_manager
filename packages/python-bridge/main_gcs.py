"""Main entry point for GCS-enabled storage manager."""

import sys
sys.path.insert(0, '/root/contabo_storage_manager/packages/python-bridge')

# Import the full app (it creates its own FastAPI instance)
from app.api_full import app

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, workers=2)
