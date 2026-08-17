"""Tests for the notes API endpoints (rain_edit / cloud_notes integration)."""

import json
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Add python-bridge to path
ROOT = Path(__file__).resolve().parents[1]
PYTHON_BRIDGE_PATH = ROOT / "packages" / "python-bridge"
if str(PYTHON_BRIDGE_PATH) not in sys.path:
    sys.path.insert(0, str(PYTHON_BRIDGE_PATH))

from app.main import app
from app import config


@pytest.fixture
def temp_files_dir(monkeypatch):
    """Temporary files directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(config.settings, "files_dir", tmpdir)
        yield tmpdir


@pytest.fixture
def client():
    """FastAPI test client."""
    return TestClient(app)


class TestNotesAPI:
    """Test the /api/notes/* endpoints."""

    def test_list_notes_empty(self, client, temp_files_dir):
        """Test listing notes when none exist."""
        response = client.get("/api/notes/list")
        assert response.status_code == 200
        assert response.json() == []

    def test_write_and_read_note(self, client, temp_files_dir):
        """Test writing and reading a note."""
        # Write a note
        write_response = client.post(
            "/api/notes/write/test-note",
            json={"content": "This is a test note."},
        )
        assert write_response.status_code == 200
        assert write_response.json()["success"] is True
        assert write_response.json()["name"] == "test-note"

        # Read the note back
        read_response = client.get("/api/notes/read/test-note")
        assert read_response.status_code == 200
        data = read_response.json()
        assert data["name"] == "test-note"
        assert data["content"] == "This is a test note."

    def test_list_notes_with_content(self, client, temp_files_dir):
        """Test listing notes shows created notes."""
        # Write a few notes
        client.post("/api/notes/write/note-one", json={"content": "First note"})
        client.post("/api/notes/write/note-two", json={"content": "Second note"})

        # List notes
        response = client.get("/api/notes/list")
        assert response.status_code == 200
        notes = response.json()
        assert len(notes) == 2
        names = [n["name"] for n in notes]
        assert "note-one" in names
        assert "note-two" in names

    def test_save_note_with_title(self, client, temp_files_dir):
        """Test saving a note with a human-readable title."""
        response = client.post(
            "/api/notes/save",
            json={
                "title": "My Project Ideas",
                "content": "- Build a widget\n- Improve documentation",
                "tags": "todo,urgent",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["title"] == "My Project Ideas"
        # Title should be slugified
        assert data["name"] == "my-project-ideas"

    def test_delete_note(self, client, temp_files_dir):
        """Test deleting a note."""
        # Write a note
        client.post("/api/notes/write/to-delete", json={"content": "temporary"})

        # Verify it exists
        response = client.get("/api/notes/read/to-delete")
        assert response.status_code == 200

        # Delete it
        delete_response = client.delete("/api/notes/delete/to-delete")
        assert delete_response.status_code == 200
        assert delete_response.json()["success"] is True

        # Verify it's gone
        response = client.get("/api/notes/read/to-delete")
        assert response.status_code == 404

    def test_path_traversal_prevention(self, client, temp_files_dir):
        """Test that path traversal attempts are blocked."""
        # Try to write to parent directory
        response = client.post(
            "/api/notes/write/../etc-passwd",
            json={"content": "malicious"},
        )
        # Starlette may normalize ".." in the URL (404). Otherwise reject or slug safely.
        assert response.status_code in [200, 400, 404]
        if response.status_code == 200:
            # If it succeeds, verify the name is slugified and safe
            name = response.json()["name"]
            assert ".." not in name
            assert "/" not in name

    def test_invalid_note_name(self, client, temp_files_dir):
        """Test that invalid note names are rejected."""
        response = client.get("/api/notes/read/note@invalid#name")
        assert response.status_code == 400

    def test_sync_note_cloud_notes_format(self, client, temp_files_dir):
        """Test syncing a note in cloud_notes payload format."""
        payload = {
            "source": "cloud_notes",
            "event": "note.updated",
            "data": {
                "id": "note-123",
                "title": "Cloud Note",
                "content": "Content from cloud_notes",
                "updatedAt": "2026-05-17T11:13:19Z",
            },
        }
        response = client.post("/api/notes/sync", json=payload)
        assert response.status_code == 200
        assert response.json()["success"] is True
        assert response.json()["title"] == "Cloud Note"

    def test_sync_batch_notes(self, client, temp_files_dir):
        """Test batch syncing multiple notes."""
        payload = {
            "notes": [
                {
                    "source": "cloud_notes",
                    "data": {
                        "id": "note-1",
                        "title": "Note One",
                        "content": "First note",
                        "updatedAt": "2026-05-17T11:13:19Z",
                    },
                },
                {
                    "source": "cloud_notes",
                    "data": {
                        "id": "note-2",
                        "title": "Note Two",
                        "content": "Second note",
                        "updatedAt": "2026-05-17T11:13:19Z",
                    },
                },
            ]
        }
        response = client.post("/api/notes/sync/batch", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["saved"] == 2
        assert data["failed"] == 0

    def test_sync_batch_partial_failure(self, client, temp_files_dir):
        """Test batch sync with one valid and one invalid note."""
        payload = {
            "notes": [
                {
                    "source": "cloud_notes",
                    "data": {
                        "id": "note-valid",
                        "title": "Valid Note",
                        "content": "This is valid",
                        "updatedAt": "2026-05-17T11:13:19Z",
                    },
                },
                {
                    "source": "cloud_notes",
                    "data": {
                        "id": "note-invalid",
                        # Missing required fields, but should still process
                        "title": "",
                    },
                },
            ]
        }
        response = client.post("/api/notes/sync/batch", json=payload)
        assert response.status_code == 200
        data = response.json()
        # Should handle gracefully
        assert "saved" in data or "failed" in data


class TestNotesWebhook:
    """Test the /webhook/notes endpoint for cloud_notes."""

    def test_webhook_notes_basic(self, client, temp_files_dir):
        """Test basic cloud_notes webhook."""
        payload = {
            "source": "cloud_notes",
            "event": "note.updated",
            "data": {
                "id": "note-webhook-123",
                "title": "Webhook Note",
                "content": "Content from webhook",
                "subject": "General",
                "section": "Inbox",
                "tags": "important",
                "author": "test-user",
                "updatedAt": "2026-05-17T11:13:19Z",
            },
        }
        response = client.post("/webhook/notes", json=payload)
        assert response.status_code == 200
        assert response.json()["status"] == "success"

    def test_webhook_notes_no_hmac_required(self, client, temp_files_dir):
        """Test that webhook notes endpoint does NOT require HMAC signature."""
        # Send without any signature header - should still work
        payload = {
            "source": "cloud_notes",
            "event": "note.updated",
            "data": {
                "id": "note-no-sig",
                "title": "No Signature",
                "content": "Should work without HMAC",
                "updatedAt": "2026-05-17T11:13:19Z",
            },
        }
        response = client.post("/webhook/notes", json=payload)
        assert response.status_code == 200

    def test_webhook_notes_encrypted_content(self, client, temp_files_dir):
        """Test webhook with encrypted content marker."""
        payload = {
            "source": "cloud_notes",
            "event": "note.updated",
            "data": {
                "id": "note-encrypted",
                "title": "Encrypted Note",
                "content": "ENC:v1:base64encodedencryptedcontent",
                "updatedAt": "2026-05-17T11:13:19Z",
            },
        }
        response = client.post("/webhook/notes", json=payload)
        assert response.status_code == 200

    def test_webhook_notes_archives_payload(self, client, temp_files_dir):
        """Test that webhook archives the raw JSON payload."""
        payload = {
            "source": "cloud_notes",
            "event": "note.updated",
            "data": {
                "id": "note-archive-test",
                "title": "Archive Test",
                "content": "Should be archived",
                "updatedAt": "2026-05-17T11:13:19Z",
            },
        }
        response = client.post("/webhook/notes", json=payload)
        assert response.status_code == 200

        # Verify webhook payload was archived
        webhook_dir = Path(temp_files_dir) / "notes" / "webhook"
        assert webhook_dir.exists()
        # Should have at least one archived payload
        archived_files = list(webhook_dir.glob("*.json"))
        assert len(archived_files) > 0

    def test_webhook_notes_missing_data(self, client, temp_files_dir):
        """Test webhook with missing data field."""
        payload = {
            "source": "cloud_notes",
            "event": "note.updated",
            # Missing 'data' field
        }
        response = client.post("/webhook/notes", json=payload)
        assert response.status_code == 422

    def test_webhook_notes_invalid_json(self, client, temp_files_dir):
        """Test webhook with invalid JSON."""
        response = client.post(
            "/webhook/notes",
            content="not valid json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422


class TestNotesSlugification:
    """Test note name slugification."""

    def test_slugify_spaces(self, client, temp_files_dir):
        """Test that spaces are converted to hyphens."""
        response = client.post(
            "/api/notes/save",
            json={"title": "My Test Note", "content": "content"},
        )
        assert response.status_code == 200
        assert response.json()["name"] == "my-test-note"

    def test_slugify_special_characters(self, client, temp_files_dir):
        """Test that special characters are removed."""
        response = client.post(
            "/api/notes/save",
            json={"title": "Note @ #$% & !!", "content": "content"},
        )
        assert response.status_code == 200
        name = response.json()["name"]
        # Should only contain alphanumeric and hyphens
        assert all(c.isalnum() or c == "-" for c in name)

    def test_slugify_unicode(self, client, temp_files_dir):
        """Test that unicode is normalized to ASCII."""
        response = client.post(
            "/api/notes/save",
            json={"title": "Café Naïve Résumé", "content": "content"},
        )
        assert response.status_code == 200
        name = response.json()["name"]
        # Should not contain non-ASCII characters
        assert name.encode("ascii")  # Should not raise UnicodeEncodeError
