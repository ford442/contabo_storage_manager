"""Tests for the /api/presets/random endpoint."""

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
PYTHON_BRIDGE_PATH = ROOT / "packages" / "python-bridge"
if str(PYTHON_BRIDGE_PATH) not in sys.path:
    sys.path.insert(0, str(PYTHON_BRIDGE_PATH))

from app.api import api_router  # noqa: E402
from app import presets  # noqa: E402
from app.config import settings  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Build a TestClient with presets_dir pointing at a temp directory."""
    monkeypatch.setattr(settings, "presets_dir", str(tmp_path))
    # Ensure the presets module looks at tmp_path for local files
    monkeypatch.setattr(
        presets,
        "_preset_index",
        {},
    )
    # Prevent load_index() from populating the index from disk
    monkeypatch.setattr(presets, "load_index", lambda: None)
    app = FastAPI()
    app.include_router(api_router)
    return TestClient(app, raise_server_exceptions=True)


def test_random_preset_empty_index_and_empty_local_returns_503(client):
    """When the index is empty and no local files exist, return 503."""
    response = client.get("/api/presets/random")
    assert response.status_code == 503
    assert "Preset index is empty" in response.json()["detail"]


def test_random_preset_returns_local_url_when_file_exists(client, tmp_path):
    """When a local preset file exists, return a local VPS URL."""
    # Create a fake preset file
    milk_dir = tmp_path / "milk"
    milk_dir.mkdir()
    preset_file = milk_dir / "test_local.milk"
    preset_file.write_text("[preset00]\nfoo=bar\n")

    # Seed the in-memory index with the same filename
    presets._preset_index["milk"] = ["test_local.milk"]

    response = client.get("/api/presets/random?dir=milk")
    assert response.status_code == 200
    data = response.json()
    assert data["dir"] == "milk"
    assert data["filename"] == "test_local.milk"
    assert "/api/presets/milk/test_local.milk" in data["url"]
    # Ensure it's NOT pointing to glsl.1ink.us
    assert "glsl.1ink.us" not in data["url"]


def test_random_preset_fallback_to_glsl_when_local_missing(client, tmp_path):
    """When the index has a file but local disk is missing it, keep the glsl URL."""
    presets._preset_index["milk"] = ["missing_on_disk.milk"]

    response = client.get("/api/presets/random?dir=milk")
    assert response.status_code == 200
    data = response.json()
    assert data["dir"] == "milk"
    assert data["filename"] == "missing_on_disk.milk"
    # Should still point to glsl because local file is absent
    assert "glsl.1ink.us" in data["url"]


def test_random_preset_local_fallback_when_index_empty(client, tmp_path):
    """When index is empty but local files exist, pick from disk."""
    presets._preset_index.clear()
    milk_dir = tmp_path / "milk"
    milk_dir.mkdir()
    preset_file = milk_dir / "fallback.milk"
    preset_file.write_text("[preset00]\n")

    response = client.get("/api/presets/random")
    assert response.status_code == 200
    data = response.json()
    assert data["dir"] == "milk"
    assert data["filename"] == "fallback.milk"
    assert "/api/presets/milk/fallback.milk" in data["url"]
