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
