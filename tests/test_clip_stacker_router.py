"""Tests for clip-stacker project save/load endpoints."""

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
PYTHON_BRIDGE_PATH = ROOT / "packages" / "python-bridge"
if str(PYTHON_BRIDGE_PATH) not in sys.path:
    sys.path.insert(0, str(PYTHON_BRIDGE_PATH))

# Patch settings before importing app to avoid permission errors during module load
from app.config import settings  # noqa: E402
test_dir = Path(__file__).parent / ".test_data"
test_dir.mkdir(parents=True, exist_ok=True)
settings.files_dir = str(test_dir)

from app.main import app  # noqa: E402

@pytest.fixture
def temp_files_dir(monkeypatch):
    """Fixture to provide a temporary files directory."""
    with TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(settings, "files_dir", tmpdir)
        # Create necessary subdirectories
        Path(tmpdir).mkdir(parents=True, exist_ok=True)
        yield tmpdir

# ==================== CORS Tests ====================

def test_clip_stacker_preflight_options_allows_cross_origin():
    """Test that OPTIONS preflight for POST returns 204."""
    client = TestClient(app)
    
    response = client.options(
        "/webhook/clip-stacker",
        headers={
            "Origin": "https://ford442.github.io",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    
    # OPTIONS preflight should return 204 No Content
    assert response.status_code == 204


def test_clip_stacker_preflight_options_for_delete():
    """Test that OPTIONS preflight for DELETE returns 204."""
    client = TestClient(app)
    
    response = client.options(
        "/webhook/clip-stacker",
        headers={
            "Origin": "https://localhost:3000",
            "Access-Control-Request-Method": "DELETE",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    
    assert response.status_code == 204


def test_clip_stacker_preflight_options_for_get():
    """Test that OPTIONS preflight for GET returns 204."""
    client = TestClient(app)
    
    response = client.options(
        "/webhook/clip-stacker",
        headers={
            "Origin": "https://ford442.github.io",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    
    assert response.status_code == 204


def test_clip_stacker_media_preflight_options():
    """Test that OPTIONS preflight for media upload returns 204."""
    client = TestClient(app)
    
    response = client.options(
        "/webhook/clip-stacker/media",
        headers={
            "Origin": "https://ford442.github.io",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    
    assert response.status_code == 204

# ==================== Functional Tests ====================

def test_clip_stacker_save_valid_project(temp_files_dir):
    """Test saving a valid clip-stacker project."""
    client = TestClient(app)
    
    project_data = {
        "name": "my-project",
        "payload": {
            "clips": [
                {"id": "clip1", "start": 0, "duration": 5},
                {"id": "clip2", "start": 5, "duration": 3},
            ],
            "transitions": [
                {"from": "clip1", "to": "clip2", "type": "fade"},
            ]
        }
    }
    
    response = client.post(
        "/webhook/clip-stacker",
        json=project_data,
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["name"] == "my-project"
    assert data["local_path"] == "clip-stacker/projects/my-project.json"
    
    # Verify file was written
    project_file = Path(temp_files_dir) / "clip-stacker" / "projects" / "my-project.json"
    assert project_file.exists()
    
    saved_data = json.loads(project_file.read_text())
    assert saved_data["name"] == "my-project"
    assert saved_data["payload"]["clips"][0]["id"] == "clip1"

def test_clip_stacker_save_with_special_characters_in_name(temp_files_dir):
    """Test that special characters in project name are sanitized."""
    client = TestClient(app)
    
    project_data = {
        "name": "my@project#name!with$special",
        "payload": {"clips": [], "transitions": []}
    }
    
    response = client.post(
        "/webhook/clip-stacker",
        json=project_data,
    )
    
    assert response.status_code == 200
    data = response.json()
    # Verify special chars are replaced with underscores
    assert "_" in data["name"] or data["name"] == "myprojectnamewithspecial"

def test_clip_stacker_save_prevents_path_traversal(temp_files_dir):
    """Test that path traversal attempts are blocked."""
    client = TestClient(app)
    
    # Attempt to traverse with ..
    project_data = {
        "name": "../../../etc/passwd",
        "payload": {"clips": [], "transitions": []}
    }
    
    response = client.post(
        "/webhook/clip-stacker",
        json=project_data,
    )
    
    # Should reject the traversal attempt
    assert response.status_code == 400
    assert "path traversal" in response.json()["detail"].lower()

def test_clip_stacker_save_missing_name(temp_files_dir):
    """Test that missing name field is rejected."""
    client = TestClient(app)
    
    project_data = {
        "payload": {"clips": [], "transitions": []}
    }
    
    response = client.post(
        "/webhook/clip-stacker",
        json=project_data,
    )
    
    assert response.status_code == 400
    assert "name" in response.json()["detail"].lower()

def test_clip_stacker_save_missing_payload(temp_files_dir):
    """Test that missing payload field is rejected."""
    client = TestClient(app)
    
    project_data = {
        "name": "my-project",
    }
    
    response = client.post(
        "/webhook/clip-stacker",
        json=project_data,
    )
    
    assert response.status_code == 400
    assert "payload" in response.json()["detail"].lower()

def test_clip_stacker_save_invalid_json(temp_files_dir):
    """Test that invalid JSON is rejected."""
    client = TestClient(app)
    
    response = client.post(
        "/webhook/clip-stacker",
        content="not valid json {",
        
    )
    
    assert response.status_code == 422
    assert "invalid json" in response.json()["detail"].lower()

def test_clip_stacker_load_valid_project(temp_files_dir):
    """Test loading a valid clip-stacker project."""
    # Create a project file
    projects_dir = Path(temp_files_dir) / "clip-stacker" / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    
    project_payload = {
        "clips": [{"id": "clip1", "start": 0}],
        "transitions": []
    }
    
    project_data = {
        "name": "test-project",
        "payload": project_payload
    }
    
    project_file = projects_dir / "test-project.json"
    project_file.write_text(json.dumps(project_data))
    
    client = TestClient(app)
    
    response = client.get(
        "/webhook/clip-stacker",
        params={"name": "test-project"},
    )
    
    assert response.status_code == 200
    data = response.json()
    assert "payload" in data
    assert data["payload"]["clips"][0]["id"] == "clip1"

def test_clip_stacker_load_missing_name(temp_files_dir):
    """Test that missing name parameter returns a list of projects."""
    client = TestClient(app)

    response = client.get("/webhook/clip-stacker")

    assert response.status_code == 200
    data = response.json()
    assert "projects" in data
    assert isinstance(data["projects"], list)

def test_clip_stacker_load_nonexistent_project(temp_files_dir):
    """Test that loading a nonexistent project returns 404."""
    client = TestClient(app)
    
    response = client.get(
        "/webhook/clip-stacker",
        params={"name": "nonexistent-project"},
    )
    
    assert response.status_code == 404

def test_clip_stacker_list_projects(temp_files_dir):
    """Test listing saved projects."""
    client = TestClient(app)

    # Save two projects
    for project_name in ["alpha-project", "beta-project"]:
        client.post(
            "/webhook/clip-stacker",
            json={"name": project_name, "payload": {"clips": [], "transitions": []}},
        )

    response = client.get("/webhook/clip-stacker")
    assert response.status_code == 200
    data = response.json()
    assert "projects" in data
    names = [p["name"] for p in data["projects"]]
    assert "alpha-project" in names
    assert "beta-project" in names
    # Should be sorted by modified time descending (newest first)
    assert data["projects"][0]["name"] == "beta-project"


def test_clip_stacker_load_prevents_path_traversal(temp_files_dir):
    """Test that path traversal attempts are blocked on load."""
    # Create a file outside the projects directory
    secret_file = Path(temp_files_dir) / "secret.json"
    secret_file.write_text(json.dumps({"secret": "value"}))
    
    client = TestClient(app)
    
    # Attempt to traverse to secret.json
    response = client.get(
        "/webhook/clip-stacker",
        params={"name": "../secret"},
    )
    
    # Should reject the traversal attempt
    assert response.status_code == 400
    assert "path traversal" in response.json()["detail"].lower()

def test_clip_stacker_save_and_load_roundtrip(temp_files_dir):
    """Test save and load roundtrip."""
    client = TestClient(app)
    
    # Save a project
    original_project = {
        "name": "roundtrip-test",
        "payload": {
            "clips": [
                {"id": "clip1", "start": 0, "duration": 5},
                {"id": "clip2", "start": 5, "duration": 3},
            ],
            "transitions": [
                {"from": "clip1", "to": "clip2", "type": "fade", "duration": 0.5},
            ]
        }
    }
    
    save_response = client.post(
        "/webhook/clip-stacker",
        content=json.dumps(original_project),
        
    )
    assert save_response.status_code == 200
    
    # Load the project
    load_response = client.get(
        "/webhook/clip-stacker",
        params={"name": "roundtrip-test"},
    )
    assert load_response.status_code == 200
    
    loaded = load_response.json()
    assert loaded["payload"] == original_project["payload"]
    assert loaded["payload"]["clips"][0]["id"] == "clip1"
    assert loaded["payload"]["transitions"][0]["type"] == "fade"

def test_clip_stacker_save_empty_payload(temp_files_dir):
    """Test saving a project with empty payload."""
    client = TestClient(app)
    
    project_data = {
        "name": "empty-project",
        "payload": {}
    }
    
    response = client.post(
        "/webhook/clip-stacker",
        json=project_data,
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    
    # Verify we can load it
    load_response = client.get(
        "/webhook/clip-stacker",
        params={"name": "empty-project"},
    )
    assert load_response.status_code == 200
    assert load_response.json()["payload"] == {}

def test_clip_stacker_load_returns_only_payload(temp_files_dir):
    """Test that GET only returns the payload, not the entire saved file."""
    # Create a project file with extra fields
    projects_dir = Path(temp_files_dir) / "clip-stacker" / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    
    project_data = {
        "name": "test-project",
        "payload": {"clips": [], "transitions": []},
        "created_at": "2023-01-01",
        "extra_field": "should not be returned"
    }
    
    project_file = projects_dir / "test-project.json"
    project_file.write_text(json.dumps(project_data))
    
    client = TestClient(app)
    
    response = client.get(
        "/webhook/clip-stacker",
        params={"name": "test-project"},
    )
    
    assert response.status_code == 200
    data = response.json()
    
    # Response should only have 'payload' key
    assert set(data.keys()) == {"payload"}
    assert data["payload"] == {"clips": [], "transitions": []}

def test_clip_stacker_save_with_dashes_and_dots(temp_files_dir):
    """Test that dashes and dots are preserved in project names."""
    client = TestClient(app)
    
    project_data = {
        "name": "my-project.v1.0",
        "payload": {"clips": [], "transitions": []}
    }
    
    response = client.post(
        "/webhook/clip-stacker",
        json=project_data,
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "my-project.v1.0"
    
    # Verify file was created with correct name
    project_file = Path(temp_files_dir) / "clip-stacker" / "projects" / "my-project.v1.0.json"
    assert project_file.exists()



def test_clip_stacker_delete_project(temp_files_dir):
    """Test deleting a clip-stacker project."""
    client = TestClient(app)

    # Save a project
    client.post(
        "/webhook/clip-stacker",
        json={"name": "delete-me", "payload": {"clips": [], "transitions": []}},
    )

    # Verify it exists
    assert (Path(temp_files_dir) / "clip-stacker" / "projects" / "delete-me.json").exists()

    response = client.delete("/webhook/clip-stacker", params={"name": "delete-me"})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"

    # Verify it's gone
    assert not (Path(temp_files_dir) / "clip-stacker" / "projects" / "delete-me.json").exists()


def test_clip_stacker_media_upload(temp_files_dir):
    """Test uploading a media file for clip-stacker."""
    client = TestClient(app)

    from io import BytesIO

    fake_file = BytesIO(b"fake video data")
    response = client.post(
        "/webhook/clip-stacker/media",
        files={"file": ("test-video.mp4", fake_file, "video/mp4")},
        data={"name": "test-video.mp4"},
    )

    assert response.status_code == 200
    data = response.json()
    assert "url" in data
    assert "clip-stacker/media/" in data["url"]
    assert data["size_bytes"] == len(b"fake video data")

    # Verify file was written
    media_dir = Path(temp_files_dir) / "clip-stacker" / "media"
    assert any(media_dir.glob("*.mp4"))


def test_clip_stacker_media_list_empty(temp_files_dir):
    """Test listing media files when directory is empty."""
    client = TestClient(app)

    response = client.get("/webhook/clip-stacker/media")

    assert response.status_code == 200
    data = response.json()
    assert "media" in data
    assert isinstance(data["media"], list)
    assert len(data["media"]) == 0


def test_clip_stacker_media_list_with_files(temp_files_dir):
    """Test listing media files when directory has files."""
    client = TestClient(app)

    # Create some media files
    media_dir = Path(temp_files_dir) / "clip-stacker" / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    
    file1 = media_dir / "clip-123-video.mp4"
    file2 = media_dir / "clip-456-audio.mp3"
    file1.write_bytes(b"video data")
    file2.write_bytes(b"audio data")

    response = client.get("/webhook/clip-stacker/media")

    assert response.status_code == 200
    data = response.json()
    assert "media" in data
    assert len(data["media"]) == 2
    
    # Check that files are listed with correct info
    files_by_name = {m["name"]: m for m in data["media"]}
    assert "clip-123-video.mp4" in files_by_name
    assert "clip-456-audio.mp3" in files_by_name
    assert files_by_name["clip-123-video.mp4"]["size"] == 10
    assert files_by_name["clip-456-audio.mp3"]["size"] == 10


def test_clip_stacker_media_list_with_prefix_filter(temp_files_dir):
    """Test listing media files with prefix filter."""
    client = TestClient(app)

    # Create some media files with different prefixes
    media_dir = Path(temp_files_dir) / "clip-stacker" / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    
    file1 = media_dir / "clip-123-video.mp4"
    file2 = media_dir / "clip-456-audio.mp3"
    file3 = media_dir / "clip-123-image.png"
    file1.write_bytes(b"video data")
    file2.write_bytes(b"audio data")
    file3.write_bytes(b"image data")

    # Filter by clip ID 123
    response = client.get("/webhook/clip-stacker/media", params={"prefix": "clip-123"})

    assert response.status_code == 200
    data = response.json()
    assert len(data["media"]) == 2
    
    names = [m["name"] for m in data["media"]]
    assert "clip-123-video.mp4" in names
    assert "clip-123-image.png" in names
    assert "clip-456-audio.mp3" not in names


def test_clip_stacker_media_delete(temp_files_dir):
    """Test deleting a media file."""
    client = TestClient(app)

    # Create a media file
    media_dir = Path(temp_files_dir) / "clip-stacker" / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    
    media_file = media_dir / "test-media.mp4"
    media_file.write_bytes(b"video data")
    
    assert media_file.exists()

    # Delete it
    response = client.delete("/webhook/clip-stacker/media/test-media.mp4")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "test-media.mp4" in data["message"]
    
    # Verify it's gone
    assert not media_file.exists()


def test_clip_stacker_media_delete_nonexistent(temp_files_dir):
    """Test deleting a non-existent media file."""
    client = TestClient(app)

    response = client.delete("/webhook/clip-stacker/media/nonexistent.mp4")

    assert response.status_code == 404


def test_clip_stacker_media_delete_path_traversal(temp_files_dir):
    """Test that path traversal is blocked in media delete."""
    client = TestClient(app)

    # Test with ".." - should be rejected
    response = client.delete("/webhook/clip-stacker/media/..mp4")
    assert response.status_code == 400  # Should be rejected due to ".."

    # Test with Windows-style backslash - should be rejected
    response2 = client.delete("/webhook/clip-stacker/media/..\\file.mp4")
    assert response2.status_code == 400  # Should be rejected due to "\\"


def test_clip_stacker_media_delete_with_slash(temp_files_dir):
    """Test that slashes are blocked in media delete."""
    client = TestClient(app)

    # FastAPI path parameter doesn't match if there's a slash in the URL path
    # A literal slash will result in 404 before our code even runs
    response = client.delete("/webhook/clip-stacker/media/subdir/file.mp4")

    # FastAPI will return 404 because the route doesn't match
    assert response.status_code == 404


def test_clip_stacker_delete_with_media_cleanup(temp_files_dir):
    """Test deleting a project with media cleanup."""
    client = TestClient(app)

    # Create a project with clips
    project_data = {
        "name": "project-with-media",
        "payload": {
            "clips": [
                {"id": "clip-uuid-1", "start": 0, "duration": 5},
                {"id": "clip-uuid-2", "start": 5, "duration": 3},
            ],
            "transitions": []
        }
    }
    
    client.post("/webhook/clip-stacker", json=project_data)

    # Create associated media files
    media_dir = Path(temp_files_dir) / "clip-stacker" / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    
    media1 = media_dir / "clip-uuid-1-original.mp4"
    media2 = media_dir / "clip-uuid-2-original.mp3"
    media3 = media_dir / "other-media.mp4"
    
    media1.write_bytes(b"media1")
    media2.write_bytes(b"media2")
    media3.write_bytes(b"media3")
    
    assert media1.exists()
    assert media2.exists()
    assert media3.exists()

    # Delete project with deleteMedia=true
    response = client.delete(
        "/webhook/clip-stacker",
        params={"name": "project-with-media", "deleteMedia": "true"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "deleted_media_count" in data
    assert data["deleted_media_count"] == 2  # Should delete 2 files
    
    # Verify media files are deleted
    assert not media1.exists()
    assert not media2.exists()
    # Other media should still exist
    assert media3.exists()


def test_clip_stacker_delete_without_media_cleanup(temp_files_dir):
    """Test deleting a project without media cleanup (default behavior)."""
    client = TestClient(app)

    # Create a project with clips
    project_data = {
        "name": "project-no-cleanup",
        "payload": {
            "clips": [
                {"id": "clip-uuid-1", "start": 0, "duration": 5},
            ],
            "transitions": []
        }
    }
    
    client.post("/webhook/clip-stacker", json=project_data)

    # Create associated media files
    media_dir = Path(temp_files_dir) / "clip-stacker" / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    
    media1 = media_dir / "clip-uuid-1-original.mp4"
    media1.write_bytes(b"media1")
    
    assert media1.exists()

    # Delete project without deleteMedia (default)
    response = client.delete(
        "/webhook/clip-stacker",
        params={"name": "project-no-cleanup"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    # Without deleteMedia, no deleted_media_count should be in response
    assert "deleted_media_count" not in data
    
    # Verify media file still exists
    assert media1.exists()
