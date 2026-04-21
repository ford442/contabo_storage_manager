import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON_BRIDGE_PATH = ROOT / "packages" / "python-bridge"
if str(PYTHON_BRIDGE_PATH) not in sys.path:
    sys.path.insert(0, str(PYTHON_BRIDGE_PATH))

from app import flac_client  # noqa: E402


def test_register_song_forwards_extended_metadata(monkeypatch):
    captured = {}

    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"id": "song-123"}

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json):
            captured["url"] = url
            captured["json"] = json
            return DummyResponse()

    monkeypatch.setattr(flac_client.settings, "flac_player_api_url", "https://example.test/api/upload/songs")
    monkeypatch.setattr(flac_client.httpx, "AsyncClient", DummyAsyncClient)

    result = asyncio.run(
        flac_client.register_song_with_flac_player(
            filename="Song.flac",
            public_url="https://storage.example/audio/music/abc.flac",
            title="Song",
            author="Artist",
            tags=["ambient", "instrumental"],
            genre="Electronic",
            duration=123.46,
            filename_on_storage="20260421T000000_song.flac",
        )
    )

    assert result == {"id": "song-123"}
    assert captured["url"] == "https://example.test/api/upload/songs"
    assert captured["json"] == {
        "name": "Song.flac",
        "title": "Song",
        "author": "Artist",
        "url": "https://storage.example/audio/music/abc.flac",
        "auto_enrich": True,
        "tags": ["ambient", "instrumental"],
        "genre": "Electronic",
        "duration": 123.46,
        "filename": "20260421T000000_song.flac",
    }
