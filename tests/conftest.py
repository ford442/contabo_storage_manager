import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
PYTHON_BRIDGE_PATH = ROOT / "packages" / "python-bridge"
if str(PYTHON_BRIDGE_PATH) not in sys.path:
    sys.path.insert(0, str(PYTHON_BRIDGE_PATH))

from app import config  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture
def temp_files_dir(monkeypatch):
    """Isolate file writes from production storage."""
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(config.settings, "files_dir", tmpdir)
        yield tmpdir


@pytest.fixture
def client(temp_files_dir):
    return TestClient(app)
