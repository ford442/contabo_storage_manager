import asyncio
import io
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from aiocache import Cache
from httpx import AsyncClient

ROOT = Path(__file__).resolve().parents[1]
PYTHON_BRIDGE_PATH = ROOT / "packages" / "python-bridge"
if str(PYTHON_BRIDGE_PATH) not in sys.path:
    sys.path.insert(0, str(PYTHON_BRIDGE_PATH))

import app.api_full as api_full  # noqa: E402


class InMemoryBlob:
    def __init__(self, name, store):
        self.name = name
        self.store = store

    def exists(self):
        return self.name in self.store

    def download_as_text(self):
        value = self.store.get(self.name)
        if value is None:
            raise FileNotFoundError(self.name)
        if isinstance(value, (bytes, bytearray)):
            return value.decode("utf-8")
        return value

    def upload_from_string(self, data, content_type=None):
        self.store[self.name] = data.encode("utf-8") if isinstance(data, str) else data

    def upload_from_file(self, file_obj, content_type=None):
        self.store[self.name] = file_obj.read()

    @property
    def public_url(self):
        return f"https://example.com/{self.name}"

    def open(self, mode="rb"):
        return io.BytesIO(self.store.get(self.name, b""))


class InMemoryBucket:
    def __init__(self):
        self.store = {}

    def blob(self, name):
        return InMemoryBlob(name, self.store)

    def list_blobs(self, prefix=""):
        return [InMemoryBlob(n, self.store) for n in self.store if n.startswith(prefix)]


@pytest.fixture(autouse=True)
def mock_storage(monkeypatch):
    bucket = InMemoryBucket()
    monkeypatch.setattr(api_full, "bucket", bucket)
    monkeypatch.setattr(api_full, "gcs_client", object())
    new_cache = Cache(Cache.MEMORY)
    monkeypatch.setattr(api_full, "cache", new_cache)
    monkeypatch.setattr(
        api_full,
        "asset_service",
        api_full.AssetService(index_lock=asyncio.Lock(), cache_obj=new_cache),
    )

    @asynccontextmanager
    async def dummy_lifespan(app):
        yield

    monkeypatch.setattr(api_full.app.router, "lifespan_context", dummy_lifespan)
    return bucket


@pytest.fixture
async def client(mock_storage):
    async with AsyncClient(app=api_full.app, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_health_check(client):
    response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "online"
    assert response.json()["gcs_connected"] is True


@pytest.mark.asyncio
async def test_upload_item_and_get_item(client):
    payload = {
        "name": "Test Song",
        "author": "Tester",
        "description": "Demo item",
        "type": "song",
        "data": {"score": 42},
    }

    upload_response = await client.post("/api/songs", json=payload)
    assert upload_response.status_code == 200
    upload_data = upload_response.json()
    assert upload_data["success"] is True
    item_id = upload_data["id"]

    get_response = await client.get(f"/api/songs/{item_id}")
    assert get_response.status_code == 200
    item = get_response.json()
    assert item["_cloud_meta"]["id"] == item_id
    assert item["_cloud_meta"]["name"] == "Test Song"
    assert item["score"] == 42


@pytest.mark.asyncio
async def test_patch_song_updates_metadata(client):
    payload = {
        "name": "Patch Song",
        "author": "Patcher",
        "description": "Original",
        "type": "song",
        "data": {"value": "x"},
    }

    upload_response = await client.post("/api/songs", json=payload)
    assert upload_response.status_code == 200
    item_id = upload_response.json()["id"]

    patch_payload = {"name": "Patched Song", "rating": 7}
    patch_response = await client.patch(f"/api/songs/{item_id}", json=patch_payload)
    assert patch_response.status_code == 200
    patched = patch_response.json()
    assert patched["status"] == "success"
    assert patched["updated"]["name"] == "Patched Song"
    assert patched["updated"]["rating"] == 7

    meta_response = await client.get(f"/api/songs/{item_id}/meta")
    assert meta_response.status_code == 200
    meta = meta_response.json()
    assert meta["name"] == "Patched Song"
    assert meta["rating"] == 7


@pytest.mark.asyncio
async def test_shader_upload_rate_list(client):
    shader_payload = {
        "name": "Example Shader",
        "description": "A test shader",
        "tags": "generative,filter",
        "author": "ShaderTester",
    }

    files = {
        "file": ("example.wgsl", "void main() { }", "text/plain"),
        "thumbnail": ("thumb.png", b"\x89PNG\r\n\x1a\n", "image/png"),
    }

    upload_response = await client.post(
        "/api/shaders/upload",
        data=shader_payload,
        files=files,
    )
    assert upload_response.status_code == 200
    shader_id = upload_response.json()["id"]

    list_response = await client.get("/api/shaders?sort_by=rating")
    assert list_response.status_code == 200
    shaders = list_response.json()
    assert any(shader["id"] == shader_id for shader in shaders)

    rate_response = await client.post(
        f"/api/shaders/{shader_id}/rate",
        data={"stars": "5"},
    )
    assert rate_response.status_code == 200
    rating = rate_response.json()
    assert rating["rating_count"] == 1
    assert float(rating["stars"]) == 5.0

    sorted_response = await client.get("/api/shaders?sort_by=rating")
    sorted_shaders = sorted_response.json()
    assert any(
        shader["id"] == shader_id and float(shader.get("stars", 0)) >= 5.0
        for shader in sorted_shaders
    )


@pytest.mark.asyncio
async def test_asset_service_read_write_and_meta(mock_storage):
    cache_obj = Cache(Cache.MEMORY)
    service = api_full.AssetService(index_lock=asyncio.Lock(), cache_obj=cache_obj)

    assert service.get_index_config("song") == api_full.STORAGE_MAP["song"]
    assert service.get_index_config("unknown") == api_full.STORAGE_MAP["default"]

    sample_index = [{"id": "sample-1", "name": "Sample Song"}]
    await service.write_index("song", sample_index)
    loaded = await service.read_index("song")
    assert loaded == sample_index

    meta = service.create_meta({"name": "Sample Song", "author": "Test"}, "song", "sample-1")
    assert meta["id"] == "sample-1"
    assert meta["type"] == "song"
    assert meta["author"] == "Test"
    assert meta["filename"] == "Sample Song"
