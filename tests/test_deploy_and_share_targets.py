import asyncio
import io
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.responses import RedirectResponse
from starlette.requests import Request

ROOT = Path(__file__).resolve().parents[1]
PYTHON_BRIDGE_PATH = ROOT / "packages" / "python-bridge"
if str(PYTHON_BRIDGE_PATH) not in sys.path:
    sys.path.insert(0, str(PYTHON_BRIDGE_PATH))

from app import deploy_router
from app.api import ShareCreateRequest, create_share, get_share
from app.config import settings


class _DummyDeployClient:
    def __init__(self):
        self.uploads = []

    def upload_bytes(self, content: bytes, target_rel: str):
        self.uploads.append((content, target_rel))


def _zip_upload(filename: str = "bundle.zip", payload_name: str = "index.html", payload: bytes = b"zip-data") -> UploadFile:
    buf = io.BytesIO()
    import zipfile

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(payload_name, payload)
    buf.seek(0)
    return UploadFile(file=buf, filename=filename)


def _make_request(accept: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/share/test",
            "query_string": b"",
            "headers": [(b"accept", accept.encode("utf-8"))],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
            "scheme": "http",
            "http_version": "1.1",
        }
    )


def test_upload_project_file_default_target_uses_test_client(monkeypatch):
    monkeypatch.setattr(settings, "deploy_base_dir", "/var/www/test.1ink.us")
    monkeypatch.setattr(settings, "deploy_auth_token", None)  # disable token check for test
    test_client = _DummyDeployClient()
    monkeypatch.setattr(deploy_router, "get_deploy_client", lambda: test_client)
    monkeypatch.setattr(deploy_router, "get_deploy_client_go", lambda: pytest.fail("go client should not be used"))

    upload = UploadFile(file=io.BytesIO(b"hello"), filename="index.html")
    result = asyncio.run(
        deploy_router.upload_project_file(
            project_name="proj",
            file=upload,
            rel_path="index.html",
            target_folder=None,
            x_deploy_token=None,
        )
    )

    assert test_client.uploads == [(b"hello", "proj/index.html")]
    assert result["target"] == "/var/www/test.1ink.us/proj/index.html"


def test_upload_project_file_go_target_requires_config(monkeypatch):
    monkeypatch.setattr(settings, "deploy_base_dir_go", None)
    monkeypatch.setattr(settings, "deploy_auth_token", None)  # disable token check for test
    monkeypatch.setattr(deploy_router, "get_deploy_client", lambda: pytest.fail("test client should not be used"))

    upload = UploadFile(file=io.BytesIO(b"hello"), filename="index.html")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            deploy_router.upload_project_file(
                project_name="proj",
                file=upload,
                rel_path="index.html",
                target_folder=None,
                target_site="go",
                x_deploy_token=None,
            )
        )

    assert exc.value.status_code == 400
    assert "DEPLOY_BASE_DIR_GO" in str(exc.value.detail)


def test_upload_project_zip_go_target_requires_config(monkeypatch):
    monkeypatch.setattr(settings, "deploy_base_dir_go", None)
    monkeypatch.setattr(settings, "deploy_auth_token", None)  # disable token check for test
    monkeypatch.setattr(deploy_router, "get_deploy_client", lambda: pytest.fail("test client should not be used"))

    upload = _zip_upload()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            deploy_router.upload_project_zip(
                project_name="proj",
                archive=upload,
                bundle=None,
                file=None,
                zipfile_upload=None,
                target_folder=None,
                target_site="go",
                x_deploy_token=None,
            )
        )

    assert exc.value.status_code == 400
    assert "DEPLOY_BASE_DIR_GO" in str(exc.value.detail)


def test_share_urls_use_flac_player_base_url(monkeypatch):
    monkeypatch.setattr(settings, "flac_player_base_url", "https://go.1ink.us/")
    monkeypatch.setattr(settings, "static_base_url", "https://storage.1ink.us")
    monkeypatch.setattr("app.api._load_songs", lambda: [{"id": "track1", "filename": "song.mp3"}])
    monkeypatch.setattr("app.api._load_shares", lambda: {})
    monkeypatch.setattr("app.api._save_shares", lambda _shares: None)

    created = asyncio.run(create_share(ShareCreateRequest(title="Test", track_ids=["track1"], expires_in_days=1)))
    assert created.full_url.startswith("https://go.1ink.us/flac-player?share=")

    monkeypatch.setattr(
        "app.api._load_shares",
        lambda: {
            "abc123": {
                "title": "Test",
                "tracks": [],
                "created_at": datetime.now(timezone.utc).isoformat(),
                "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            }
        },
    )
    redirect = asyncio.run(get_share("abc123", _make_request("text/html")))

    assert isinstance(redirect, RedirectResponse)
    assert redirect.headers["location"] == "https://go.1ink.us/flac-player?share=abc123"
