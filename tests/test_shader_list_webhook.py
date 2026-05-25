import os
import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
PYTHON_BRIDGE_PATH = ROOT / "packages" / "python-bridge"
if str(PYTHON_BRIDGE_PATH) not in sys.path:
    sys.path.insert(0, str(PYTHON_BRIDGE_PATH))
os.environ.setdefault("FILES_DIR", str(ROOT / ".test-files"))

import app.webhooks as webhooks_module  # noqa: E402
from app.webhooks import webhook_router  # noqa: E402


def _build_client():
    app = FastAPI()
    app.include_router(webhook_router)
    return TestClient(app, raise_server_exceptions=True)


def test_generate_shader_lists_requires_token(monkeypatch):
    monkeypatch.setattr(webhooks_module.settings, "shader_generation_token", "top-secret")

    client = _build_client()
    response = client.post("/webhook/image-effects/generate-shader-lists")

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid webhook token"


def test_generate_shader_lists_requires_server_configuration(monkeypatch):
    monkeypatch.setattr(webhooks_module.settings, "shader_generation_token", None)

    client = _build_client()
    response = client.post(
        "/webhook/image-effects/generate-shader-lists",
        headers={"X-Webhook-Token": "anything"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Shader generation token is not configured"


def test_generate_shader_lists_runs_and_uploads(monkeypatch, tmp_path):
    repo_dir = tmp_path / "image_video_effects"
    script_path = repo_dir / "scripts" / "generate_shader_lists.js"
    shader_lists_dir = repo_dir / "shader-lists"
    storage_dir = tmp_path / "storage"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text("// fake script", encoding="utf-8")
    (repo_dir / ".git").mkdir(parents=True, exist_ok=True)
    shader_lists_dir.mkdir(parents=True, exist_ok=True)
    (shader_lists_dir / "all.json").write_text('{"ok":1}', encoding="utf-8")
    (shader_lists_dir / "featured.json").write_text('{"ok":2}', encoding="utf-8")

    monkeypatch.setattr(webhooks_module.settings, "shader_generation_token", "top-secret")
    monkeypatch.setattr(webhooks_module.settings, "webhook_secret", None)
    monkeypatch.setattr(webhooks_module.settings, "image_effects_repo_dir", str(repo_dir))
    monkeypatch.setattr(webhooks_module.settings, "image_effects_shader_lists_dir", "shader-lists")
    monkeypatch.setattr(webhooks_module.settings, "files_dir", str(storage_dir))

    commands = []

    def fake_run(cmd, **kwargs):
        commands.append((cmd, kwargs))
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(webhooks_module.subprocess, "run", fake_run)
    to_thread_calls = []

    async def fake_to_thread(func, *args, **kwargs):
        to_thread_calls.append(getattr(func, "__name__", str(func)))
        return func(*args, **kwargs)

    monkeypatch.setattr(webhooks_module.asyncio, "to_thread", fake_to_thread)

    uploaded = []

    async def fake_upload(local_path, rel_path):
        uploaded.append((str(local_path), rel_path))
        return f"/remote/{rel_path}"

    monkeypatch.setattr(webhooks_module.ftp_client, "upload", fake_upload)

    client = _build_client()
    response = client.post(
        "/webhook/image-effects/generate-shader-lists",
        headers={"X-Webhook-Token": "top-secret"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["files"] == [
        "image-effects/shader-lists/all.json",
        "image-effects/shader-lists/featured.json",
    ]
    assert payload["remote_files"] == [
        "/remote/image-effects/shader-lists/all.json",
        "/remote/image-effects/shader-lists/featured.json",
    ]

    assert commands[0][0] == ["git", "-C", str(repo_dir), "pull", "--ff-only"]
    assert commands[1][0] == ["node", str(script_path)]

    assert (storage_dir / "image-effects" / "shader-lists" / "all.json").exists()
    assert (storage_dir / "image-effects" / "shader-lists" / "featured.json").exists()
    assert [item[1] for item in uploaded] == [
        "image-effects/shader-lists/all.json",
        "image-effects/shader-lists/featured.json",
    ]
    assert to_thread_calls.count("fake_run") == 2
    assert to_thread_calls.count("copy2") == 2


def test_generate_shader_lists_returns_error_when_git_pull_fails(monkeypatch, tmp_path):
    repo_dir = tmp_path / "image_video_effects"
    script_path = repo_dir / "scripts" / "generate_shader_lists.js"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text("// fake script", encoding="utf-8")
    (repo_dir / ".git").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(webhooks_module.settings, "shader_generation_token", "top-secret")
    monkeypatch.setattr(webhooks_module.settings, "image_effects_repo_dir", str(repo_dir))
    monkeypatch.setattr(webhooks_module.settings, "image_effects_shader_lists_dir", "shader-lists")

    def fake_run(cmd, **kwargs):
        if cmd[:4] == ["git", "-C", str(repo_dir), "pull"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="pull failed")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(webhooks_module.subprocess, "run", fake_run)

    client = _build_client()
    response = client.post(
        "/webhook/image-effects/generate-shader-lists",
        headers={"X-Webhook-Token": "top-secret"},
    )

    assert response.status_code == 500
    assert "git pull failed" in response.json()["detail"]


def test_generate_shader_lists_returns_error_when_script_fails(monkeypatch, tmp_path):
    repo_dir = tmp_path / "image_video_effects"
    script_path = repo_dir / "scripts" / "generate_shader_lists.js"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text("// fake script", encoding="utf-8")
    (repo_dir / ".git").mkdir(parents=True, exist_ok=True)
    (repo_dir / "shader-lists").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(webhooks_module.settings, "shader_generation_token", "top-secret")
    monkeypatch.setattr(webhooks_module.settings, "image_effects_repo_dir", str(repo_dir))
    monkeypatch.setattr(webhooks_module.settings, "image_effects_shader_lists_dir", "shader-lists")

    def fake_run(cmd, **kwargs):
        if cmd[0] == "node":
            return SimpleNamespace(returncode=1, stdout="", stderr="script failed")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(webhooks_module.subprocess, "run", fake_run)

    client = _build_client()
    response = client.post(
        "/webhook/image-effects/generate-shader-lists",
        headers={"X-Webhook-Token": "top-secret"},
    )

    assert response.status_code == 500
    assert "shader list generation failed" in response.json()["detail"]
