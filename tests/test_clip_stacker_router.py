import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Assuming your FastAPI app is imported like this
from app.main import app  # Adjust import as needed


@pytest.fixture
def client(temp_files_dir):
    """Provide a test client with the app."""
    return TestClient(app)


# =============================================================================
# PROJECT TESTS
# =============================================================================

def test_clip_stacker_save_valid_project(client, temp_files_dir):
    """Test saving a valid clip-stacker project."""
    project_data = {
        "name": "my-project",
        "payload": {
            "clips": [
                {"id": "clip1", "start": 0, "duration": 5},
                {"id": "clip2", "start": 5, "duration": 3},
            ],
            "transitions": [
                {"from": "clip1", "to": "clip2", "type": "fade"},
            ],
        },
    }

    response = client.post("/webhook/clip-stacker", json=project_data)
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "success"
    assert data["name"] == "my-project"
    assert "local_path" in data

    # Verify file was written
    project_file = Path(temp_files_dir) / "clip-stacker" / "projects" / "my-project.json"
    assert project_file.exists()

    saved_data = json.loads(project_file.read_text())
    assert saved_data["name"] == "my-project"
    assert saved_data["payload"]["clips"][0]["id"] == "clip1"


def test_clip_stacker_save_sanitizes_name(client):
    """Test that special characters in project name are sanitized."""
    project_data = {
        "name": "my@project#name!with$special",
        "payload": {"clips": [], "transitions": []},
    }

    response = client.post("/webhook/clip-stacker", json=project_data)
    assert response.status_code == 200
    data = response.json()
    # Should sanitize dangerous characters
    assert data["name"] == "my_project_name_with_special"


def test_clip_stacker_save_prevents_path_traversal(client):
    """Test that path traversal attempts are blocked on save."""
    project_data = {
        "name": "../../../etc/passwd",
        "payload": {"clips": [], "transitions": []},
    }

    response = client.post("/webhook/clip-stacker", json=project_data)
    assert response.status_code == 400
    assert "path traversal" in response.json()["detail"].lower()


def test_clip_stacker_save_missing_fields(client):
    """Test validation for missing required fields."""
    # Missing name
    response = client.post("/webhook/clip-stacker", json={"payload": {}})
    assert response.status_code == 400

    # Missing payload
    response = client.post("/webhook/clip-stacker", json={"name": "test"})
    assert response.status_code == 400


def test_clip_stacker_save_invalid_json(client):
    """Test that invalid JSON is rejected."""
    response = client.post(
        "/webhook/clip-stacker",
        content="not valid json {",
    )
    assert response.status_code == 422


def test_clip_stacker_load_valid_project(client, temp_files_dir):
    """Test loading a valid project."""
    projects_dir = Path(temp_files_dir) / "clip-stacker" / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)

    project_data = {
        "name": "test-project",
        "payload": {"clips": [{"id": "clip1", "start": 0}], "transitions": []},
    }
    (projects_dir / "test-project.json").write_text(json.dumps(project_data))

    response = client.get("/webhook/clip-stacker", params={"name": "test-project"})
    assert response.status_code == 200
    assert response.json()["payload"]["clips"][0]["id"] == "clip1"


def test_clip_stacker_load_returns_only_payload(client, temp_files_dir):
    """Ensure GET only returns the payload, not internal fields."""
    projects_dir = Path(temp_files_dir) / "clip-stacker" / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)

    full_data = {
        "name": "test-project",
        "payload": {"clips": [], "transitions": []},
        "created_at": "2023-01-01",
        "extra_field": "should not be returned",
    }
    (projects_dir / "test-project.json").write_text(json.dumps(full_data))

    response = client.get("/webhook/clip-stacker", params={"name": "test-project"})
    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"payload"}


def test_clip_stacker_list_projects(client):
    """Test listing projects (should be sorted by modified time descending)."""
    for name in ["alpha-project", "beta-project"]:
        client.post("/webhook/clip-stacker", json={"name": name, "payload": {}})

    response = client.get("/webhook/clip-stacker")
    assert response.status_code == 200
    names = [p["name"] for p in response.json()["projects"]]
    assert names[0] == "beta-project"  # Newest first


def test_clip_stacker_delete_project(client):
    """Test deleting a project."""
    client.post("/webhook/clip-stacker", json={"name": "delete-me", "payload": {}})
    response = client.delete("/webhook/clip-stacker", params={"name": "delete-me"})
    assert response.status_code == 200
    assert response.json()["status"] == "success"


# =============================================================================
# MEDIA TESTS
# =============================================================================

def test_clip_stacker_media_upload(client, temp_files_dir):
    """Test uploading media."""
    fake_file = b"fake video data"
    response = client.post(
        "/webhook/clip-stacker/media",
        files={"file": ("test-video.mp4", fake_file, "video/mp4")},
        data={"name": "test-video.mp4"},
    )
    assert response.status_code == 200
    assert "url" in response.json()


def test_clip_stacker_media_list(client, temp_files_dir):
    """Test listing media files with optional prefix filter."""
    media_dir = Path(temp_files_dir) / "clip-stacker" / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    (media_dir / "clip-123-video.mp4").write_bytes(b"video")
    (media_dir / "clip-123-image.png").write_bytes(b"image")
    (media_dir / "clip-456-audio.mp3").write_bytes(b"audio")

    # No filter
    response = client.get("/webhook/clip-stacker/media")
    assert len(response.json()["media"]) == 3

    # With prefix filter
    response = client.get("/webhook/clip-stacker/media", params={"prefix": "clip-123"})
    assert len(response.json()["media"]) == 2


def test_clip_stacker_media_delete(client, temp_files_dir):
    """Test deleting a media file."""
    media_dir = Path(temp_files_dir) / "clip-stacker" / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    media_file = media_dir / "test-media.mp4"
    media_file.write_bytes(b"data")

    response = client.delete("/webhook/clip-stacker/media/test-media.mp4")
    assert response.status_code == 200
    assert not media_file.exists()


def test_clip_stacker_media_delete_prevents_path_traversal(client):
    """Test path traversal protection on media delete."""
    for malicious_name in ["../file.mp4", "..\\file.mp4"]:
        response = client.delete(f"/webhook/clip-stacker/media/{malicious_name}")
        assert response.status_code == 400


def test_clip_stacker_delete_project_with_media_cleanup(client, temp_files_dir):
    """Test that deleting a project can also clean up associated media."""
    # Create project
    client.post(
        "/webhook/clip-stacker",
        json={
            "name": "project-with-media",
            "payload": {"clips": [{"id": "clip-uuid-1"}]},
        },
    )

    # Create media files
    media_dir = Path(temp_files_dir) / "clip-stacker" / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    (media_dir / "clip-uuid-1-original.mp4").write_bytes(b"data")
    (media_dir / "other-media.mp4").write_bytes(b"data")

    response = client.delete(
        "/webhook/clip-stacker",
        params={"name": "project-with-media", "deleteMedia": "true"},
    )
    assert response.status_code == 200
    assert response.json()["deleted_media_count"] == 1
    assert not (media_dir / "clip-uuid-1-original.mp4").exists()