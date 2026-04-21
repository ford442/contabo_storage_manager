import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
PYTHON_BRIDGE_PATH = ROOT / "packages" / "python-bridge"
if str(PYTHON_BRIDGE_PATH) not in sys.path:
    sys.path.insert(0, str(PYTHON_BRIDGE_PATH))

from app import api as api_module  # noqa: E402
from app.api import api_router  # noqa: E402


def create_client() -> TestClient:
    app = FastAPI()
    app.include_router(api_router)
    return TestClient(app)


def _sample_songs():
    return [
        {
            "id": "1",
            "name": "song.flac",
            "title": "Song",
            "author": "Artist",
            "genre": "Rock",
            "rating": None,
            "description": "Test song",
            "tags": [],
            "duration": 120,
            "play_count": 0,
            "last_played": None,
            "created_at": "2026-04-21T12:00:00Z",
            "url": "/api/music/1",
            "size": 1234,
            "filename": "1_Song.flac",
        },
        {
            "id": "2",
            "name": "song2.flac",
            "title": "Song 2",
            "author": "Artist",
            "genre": "Pop",
            "rating": 5,
            "description": "Test song 2",
            "tags": ["pop"],
            "duration": 150,
            "play_count": 0,
            "last_played": None,
            "created_at": "2026-04-21T12:00:00Z",
            "url": "/api/music/2",
            "size": 2345,
            "filename": "2_Song_2.flac",
        },
    ]


def test_rating_gte_zero_is_accepted(monkeypatch):
    monkeypatch.setattr(api_module, "_load_songs", lambda: _sample_songs())
    response = create_client().get("/api/songs?rating_gte=0")

    assert response.status_code == 200
    assert len(response.json()) == 2


def test_rating_lt_zero_is_accepted(monkeypatch):
    monkeypatch.setattr(api_module, "_load_songs", lambda: _sample_songs())
    response = create_client().get("/api/songs?rating_lt=0")

    assert response.status_code == 200
    assert response.json() == []
