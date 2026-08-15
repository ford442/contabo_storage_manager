"""Tests for MOD library endpoints used by mod-player."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "python-bridge"))


@pytest.fixture
def mods_env(tmp_path, monkeypatch):
    """Isolated FILES_DIR with a couple of fake tracker files."""
    files_dir = tmp_path / "files"
    mods_dir = files_dir / "mods"
    mods_dir.mkdir(parents=True)

    (mods_dir / "demo_tune.xm").write_bytes(b"Extended Module: fake" + b"\x00" * 64)
    (mods_dir / "chip_song.mod").write_bytes(b"M.K." + b"\x00" * 64)
    (files_dir / "songs.json").write_text(json.dumps({"songs": []}))

    from app import config as config_module
    from app import mod_router as mod_module
    from app import api as api_module

    monkeypatch.setattr(config_module.settings, "files_dir", str(files_dir))
    monkeypatch.setattr(config_module.settings, "static_base_url", "https://storage.example.com/files")
    monkeypatch.setattr(api_module.settings, "files_dir", str(files_dir))
    monkeypatch.setattr(api_module.settings, "static_base_url", "https://storage.example.com/files")
    monkeypatch.setattr(mod_module, "_index_cache", None)
    monkeypatch.setattr(mod_module, "_index_mtime", None)
    monkeypatch.setattr(api_module, "_songs_cache", None)
    monkeypatch.setattr(api_module, "_songs_cache_mtime", None)

    # Avoid openmpt123 dependency in unit tests
    monkeypatch.setattr(
        mod_module,
        "_extract_mod_metadata",
        lambda path: {
            "title": path.stem.title().replace("_", " "),
            "author": "Test",
            "duration": 12.5,
        },
    )

    # Real local scan without FTP
    def local_scan(pull_remote=True, extract_metadata=True):
        mods = mod_module._mods_dir()
        index = mod_module._load_index()
        scanned = added = updated = 0
        now = datetime.now(timezone.utc).isoformat()
        for filepath in mods.iterdir():
            if not filepath.is_file() or filepath.suffix.lower() not in mod_module.MOD_EXTENSIONS:
                continue
            scanned += 1
            filename = filepath.name
            file_id = mod_module._file_id(filename)
            meta = mod_module._extract_mod_metadata(filepath)
            if file_id not in index:
                index[file_id] = {
                    "id": file_id,
                    "filename": filename,
                    "title": meta["title"] or filepath.stem,
                    "author": meta["author"],
                    "duration": meta["duration"],
                    "size": filepath.stat().st_size,
                    "tags": [],
                    "notes": "",
                    "url": mod_module._public_url(filename),
                    "added_at": now,
                    "updated_at": now,
                }
                added += 1
            else:
                updated += 1
        mod_module._save_index(index)
        return mod_module.ScanResult(
            scanned=scanned, added=added, updated=updated, total=len(index), message="ok"
        )

    monkeypatch.setattr(mod_module, "_scan_mods_sync", local_scan)

    app = FastAPI()
    app.include_router(mod_module.mod_router)
    app.include_router(api_module.api_router)
    return TestClient(app), files_dir, mods_dir


def test_list_mods_auto_indexes_empty_index(mods_env):
    client, _files_dir, _mods_dir = mods_env
    res = client.get("/api/mods")
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 2
    ids = {m["id"] for m in data}
    assert "demo_tune" in ids
    assert "chip_song" in ids
    for m in data:
        assert m["url"].startswith("https://storage.example.com/files/mods/")
        assert m["download_url"].endswith(f"/api/mods/{m['id']}/download")
        assert m["downloadUrl"] == m["download_url"]
        assert m["type"] == "mod"
        assert m["fileName"]
        assert m["name"]


def test_list_mods_rewrites_stale_urls(mods_env):
    client, _files_dir, mods_dir = mods_env
    index = {
        "demo_tune": {
            "id": "demo_tune",
            "filename": "demo_tune.xm",
            "title": "Demo",
            "author": "Someone",
            "duration": 10.0,
            "size": 10,
            "tags": [],
            "notes": "",
            "url": "https://storage.1ink.us/mods/demo_tune.xm",
            "added_at": "2020-01-01",
            "updated_at": "2020-01-01",
        }
    }
    (mods_dir / "index.json").write_text(json.dumps(index))
    from app import mod_router as mod_module
    mod_module._invalidate_index_cache()

    res = client.get("/api/mods")
    assert res.status_code == 200
    demo = next(m for m in res.json() if m["id"] == "demo_tune")
    assert demo["url"] == "https://storage.example.com/files/mods/demo_tune.xm"
    assert "storage.1ink.us" not in demo["url"]


def test_songs_type_mod(mods_env):
    client, _files_dir, _mods_dir = mods_env
    client.get("/api/mods")  # ensure index
    res = client.get("/api/songs?type=mod")
    assert res.status_code == 200
    data = res.json()
    assert len(data) >= 2
    assert all(s.get("type") == "mod" for s in data)
    assert all(s.get("url") for s in data)
    # FastAPI must not strip mod_router aliases (mod-player RemoteSong).
    assert all(s.get("download_url") for s in data)
    assert all(s.get("downloadUrl") == s.get("download_url") for s in data)
    assert all(s.get("filename") for s in data)
    assert all(s.get("fileName") == s.get("filename") for s in data)


def test_scan_post_and_get(mods_env):
    client, _files_dir, _mods_dir = mods_env
    for method in ("get", "post"):
        res = getattr(client, method)("/api/mods/scan")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["total"] >= 2
        assert "scanned" in body
