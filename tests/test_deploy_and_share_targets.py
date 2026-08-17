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
    def __init__(self, remote_sizes=None):
        self.uploads = []
        self.remote_sizes = dict(remote_sizes or {})

    def upload_bytes(self, content: bytes, target_rel: str):
        self.uploads.append((content, target_rel))
        # After upload, remote size matches what we just sent
        rel = target_rel.split("/", 1)[1] if "/" in target_rel else target_rel
        self.remote_sizes[rel] = len(content)

    def remote_file_size(self, remote_rel_path: str):
        rel = remote_rel_path.split("/", 1)[1] if "/" in remote_rel_path else remote_rel_path
        return self.remote_sizes.get(rel)

    def list_file_sizes(self, rel_dir: str):
        return dict(self.remote_sizes)


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


def test_upload_project_file_prod_target_requires_config(monkeypatch):
    monkeypatch.setattr(settings, "deploy_base_dir_prod", None)
    monkeypatch.setattr(settings, "deploy_auth_token", None)
    monkeypatch.setattr(deploy_router, "get_deploy_client", lambda: pytest.fail("test client should not be used"))

    upload = UploadFile(file=io.BytesIO(b"hello"), filename="index.html")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            deploy_router.upload_project_file(
                project_name="proj",
                file=upload,
                rel_path="index.html",
                target_folder=None,
                target_site="prod",
                x_deploy_token=None,
            )
        )

    assert exc.value.status_code == 400
    assert "DEPLOY_BASE_DIR_PROD" in str(exc.value.detail)


def test_upload_project_file_prod_target_uses_prod_client(monkeypatch):
    monkeypatch.setattr(settings, "deploy_base_dir_prod", "/home/ford442")
    monkeypatch.setattr(settings, "deploy_auth_token", None)
    prod_client = _DummyDeployClient()
    monkeypatch.setattr(deploy_router, "get_deploy_client_prod", lambda: prod_client)
    monkeypatch.setattr(deploy_router, "get_deploy_client", lambda: pytest.fail("test client should not be used"))
    monkeypatch.setattr(deploy_router, "get_deploy_client_go", lambda: pytest.fail("go client should not be used"))

    upload = UploadFile(file=io.BytesIO(b"hello"), filename="index.html")
    result = asyncio.run(
        deploy_router.upload_project_file(
            project_name="project-m",
            file=upload,
            rel_path="1ink.1ink",
            target_folder="projectm.1ink.us",
            target_site="prod",
            x_deploy_token=None,
        )
    )

    assert prod_client.uploads == [(b"hello", "projectm.1ink.us/1ink.1ink")]
    assert result["target"] == "/home/ford442/projectm.1ink.us/1ink.1ink"
    assert result["target_site"] == "prod"


def test_upload_bytes_with_retries_recovers_after_transient_error(monkeypatch):
    attempts = {"n": 0}

    class _FlakyClient:
        def upload_bytes(self, content, target_rel):
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise RuntimeError("Server connection dropped: ")
            return True

        def close(self):
            pass

    deploy_router._upload_bytes_with_retries(_FlakyClient(), b"x", "proj/a.txt")
    assert attempts["n"] == 2


def test_upload_project_zip_skips_identical_size(monkeypatch):
    monkeypatch.setattr(settings, "deploy_base_dir", "/var/www/test.1ink.us")
    monkeypatch.setattr(settings, "deploy_auth_token", None)
    client = _DummyDeployClient(remote_sizes={"index.html": len(b"zip-data")})
    monkeypatch.setattr(deploy_router, "get_deploy_client", lambda: client)

    result = asyncio.run(
        deploy_router.upload_project_zip(
            project_name="proj",
            archive=_zip_upload(),
            bundle=None,
            file=None,
            zipfile_upload=None,
            target_folder=None,
            x_deploy_token=None,
        )
    )

    assert client.uploads == []
    assert result["uploaded"] == 0
    assert result["skipped"] == 1
    assert result["skipped_files"] == ["index.html"]


def test_upload_project_file_skips_identical_size(monkeypatch):
    monkeypatch.setattr(settings, "deploy_base_dir", "/var/www/test.1ink.us")
    monkeypatch.setattr(settings, "deploy_auth_token", None)
    client = _DummyDeployClient(remote_sizes={"index.html": 5})
    monkeypatch.setattr(deploy_router, "get_deploy_client", lambda: client)

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

    assert client.uploads == []
    assert result["skipped"] is True
    assert result["size"] == 5


def test_list_project_remote_sizes(monkeypatch):
    monkeypatch.setattr(settings, "deploy_base_dir", "/var/www/test.1ink.us")
    monkeypatch.setattr(settings, "deploy_auth_token", None)
    client = _DummyDeployClient(remote_sizes={"js/app.js": 42, "index.html": 9})
    monkeypatch.setattr(deploy_router, "get_deploy_client", lambda: client)

    result = asyncio.run(
        deploy_router.list_project_remote_sizes(
            project_name="proj",
            target_folder=None,
            x_deploy_token=None,
        )
    )

    assert result["count"] == 2
    assert result["files"]["js/app.js"] == 42
    assert result["target_prefix"] == "proj"


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
