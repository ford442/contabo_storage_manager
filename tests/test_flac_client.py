import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
PYTHON_BRIDGE_PATH = ROOT / "packages" / "python-bridge"
if str(PYTHON_BRIDGE_PATH) not in sys.path:
    sys.path.insert(0, str(PYTHON_BRIDGE_PATH))

from app import flac_client  # noqa: E402


@pytest.mark.asyncio
async def test_register_song_forwards_extended_metadata(monkeypatch):
    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"id": "song-123"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["payload"] = kwargs["json"]
            return FakeResponse()

    monkeypatch.setattr(
        flac_client.settings,
        "flac_player_api_url",
        "https://flac.example/api/upload/songs",
    )
    monkeypatch.setattr(flac_client.httpx, "AsyncClient", FakeAsyncClient)

    result = await flac_client.register_song_with_flac_player(
        filename="My Song.flac",
        public_url="https://storage.example/audio/music/abc.flac",
        title="My Song",
        author="Artist",
        tags=["rock", "upbeat"],
        genre="Rock",
        duration=182.5,
        filename_on_storage="abc.flac",
    )

    assert result == {"id": "song-123"}
    assert captured["url"] == "https://flac.example/api/upload/songs"

    payload = captured["payload"]
    assert payload["name"] == "My Song.flac"
    assert payload["title"] == "My Song"
    assert payload["author"] == "Artist"
    assert payload["url"] == "https://storage.example/audio/music/abc.flac"
    assert payload.get("auto_enrich") is True
    assert payload["tags"] == ["rock", "upbeat"]
    assert payload["genre"] == "Rock"
    assert payload["duration"] == 182.5
    assert payload["filename"] == "abc.flac"


@pytest.mark.asyncio
async def test_register_song_returns_none_when_url_not_configured(monkeypatch):
    monkeypatch.setattr(flac_client.settings, "flac_player_api_url", "")

    result = await flac_client.register_song_with_flac_player(
        filename="song.flac",
        public_url="https://storage.example/audio/music/song.flac",
    )

    assert result is None