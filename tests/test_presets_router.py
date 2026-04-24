"""Tests for the presets router (/api/presets)."""

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
PYTHON_BRIDGE_PATH = ROOT / "packages" / "python-bridge"
if str(PYTHON_BRIDGE_PATH) not in sys.path:
    sys.path.insert(0, str(PYTHON_BRIDGE_PATH))

from app.presets_router import presets_router, PRESET_DIRS  # noqa: E402
import app.presets_router as presets_module  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Build a TestClient with presets_dir pointing at a temp directory."""
    monkeypatch.setattr(presets_module.settings, "presets_dir", str(tmp_path))
    # Rebuild the static dir map to point at tmp_path
    monkeypatch.setattr(
        presets_module,
        "_PRESET_DIR_MAP",
        {name: tmp_path / name for name in PRESET_DIRS},
    )
    app = FastAPI()
    app.include_router(presets_router)
    return TestClient(app, raise_server_exceptions=True)


# ── list preset dirs ────────────────────────────────────────────────────────────

def test_list_dirs_returns_all_five(client):
    response = client.get("/api/presets/")
    assert response.status_code == 200
    data = response.json()
    names = [d["name"] for d in data]
    for expected in PRESET_DIRS:
        assert expected in names


def test_list_dirs_count_zero_initially(client):
    response = client.get("/api/presets/")
    assert response.status_code == 200
    for entry in response.json():
        assert entry["count"] == 0
        assert entry["updated_at"] is None


# ── list files in a dir ─────────────────────────────────────────────────────────

def test_list_files_empty(client):
    response = client.get("/api/presets/milk")
    assert response.status_code == 200
    assert response.json() == []


def test_list_files_unknown_dir_returns_400(client):
    response = client.get("/api/presets/evil_dir")
    assert response.status_code == 400


def test_list_files_shows_uploaded_file(client):
    # Upload a preset first
    client.post(
        "/api/presets/milk",
        json={"filename": "test.milk", "content": "[preset00]\nfoo=bar\n"},
    )
    response = client.get("/api/presets/milk")
    assert response.status_code == 200
    files = response.json()
    assert len(files) == 1
    assert files[0]["name"] == "test.milk"
    assert files[0]["size"] > 0


# ── get a file ──────────────────────────────────────────────────────────────────

def test_get_file_returns_content(client):
    content = "[preset00]\nwarp=0.5\n"
    client.post(
        "/api/presets/milkSML",
        json={"filename": "warp.milk", "content": content},
    )
    response = client.get("/api/presets/milkSML/warp.milk")
    assert response.status_code == 200
    assert response.text == content


def test_get_file_not_found(client):
    response = client.get("/api/presets/milk/nonexistent.milk")
    assert response.status_code == 404


def test_get_file_unknown_dir_returns_400(client):
    response = client.get("/api/presets/bad_dir/test.milk")
    assert response.status_code == 400


# ── upload a file ───────────────────────────────────────────────────────────────

def test_upload_creates_file(client, tmp_path):
    response = client.post(
        "/api/presets/custom_milk",
        json={"filename": "my_preset.milk", "content": "[preset00]\n"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["success"] is True
    assert data["filename"] == "my_preset.milk"
    assert (tmp_path / "custom_milk" / "my_preset.milk").exists()


def test_upload_unknown_dir_returns_400(client):
    response = client.post(
        "/api/presets/evil",
        json={"filename": "x.milk", "content": ""},
    )
    assert response.status_code == 400


def test_upload_non_milk_extension_rejected(client):
    response = client.post(
        "/api/presets/milk",
        json={"filename": "hack.exe", "content": ""},
    )
    assert response.status_code == 400


def test_upload_path_traversal_rejected(client):
    response = client.post(
        "/api/presets/milk",
        json={"filename": "../evil.milk", "content": ""},
    )
    assert response.status_code == 400


# ── delete a file ───────────────────────────────────────────────────────────────

def test_delete_file(client, tmp_path):
    client.post(
        "/api/presets/milk",
        json={"filename": "todelete.milk", "content": "foo"},
    )
    response = client.delete("/api/presets/milk/todelete.milk")
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert not (tmp_path / "milk" / "todelete.milk").exists()


def test_delete_nonexistent_returns_404(client):
    response = client.delete("/api/presets/milk/ghost.milk")
    assert response.status_code == 404


def test_delete_unknown_dir_returns_400(client):
    response = client.delete("/api/presets/bad/file.milk")
    assert response.status_code == 400


# ── count reflects uploads ───────────────────────────────────────────────────────

def test_count_updates_after_upload(client):
    client.post(
        "/api/presets/milkMED",
        json={"filename": "a.milk", "content": "x"},
    )
    client.post(
        "/api/presets/milkMED",
        json={"filename": "b.milk", "content": "y"},
    )
    response = client.get("/api/presets/")
    data = {d["name"]: d for d in response.json()}
    assert data["milkMED"]["count"] == 2
    assert data["milkMED"]["updated_at"] is not None

