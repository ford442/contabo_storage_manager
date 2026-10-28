"""Tests for /webhook/clip-stacker/jobs (extract / splice / rife workers).

The worker tests run real ffmpeg/ffprobe on small generated clips.
"""
import asyncio
import json
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app import clip_stacker_jobs as jobs
from app.config import settings

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed",
)

JOBS = "/webhook/clip-stacker/jobs"


def _make_clip(path: Path, seconds: int = 10, *, gop: int = 50, size: str = "320x240", fps: int = 25, audio: bool = True):
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate={fps}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"]
    cmd += ["-t", str(seconds), "-c:v", "libx264", "-g", str(gop), "-keyint_min", str(gop),
            "-sc_threshold", "0", "-bf", "2", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac", "-shortest"]
    subprocess.run(cmd + [str(path)], check=True)


def _duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    return float(out.strip())


@pytest.fixture
def media(temp_files_dir):
    media_dir = Path(temp_files_dir) / "clip-stacker" / "media"
    media_dir.mkdir(parents=True)
    _make_clip(media_dir / "src.mp4")  # 10s, keyframes every 2s
    return media_dir


@pytest.fixture
def queued(monkeypatch):
    """Capture scheduled jobs instead of spawning tasks, so tests run the worker explicitly."""
    calls = []
    monkeypatch.setattr(jobs, "_schedule", lambda job_id, secrets: calls.append((job_id, secrets)))
    return calls


def _submit(client, queued, body):
    resp = client.post(JOBS, json=body)
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["jobId"]
    assert resp.json()["status"] == "queued"
    _, secrets = queued[-1]
    return job_id, secrets


def _run(client, job_id, secrets):
    asyncio.run(jobs._run_job(job_id, secrets))
    return client.get(f"{JOBS}/{job_id}").json()


# ---------- validation / auth ----------
@pytest.mark.parametrize(
    "body",
    [
        {"type": "extract", "source": {"kind": "media", "name": "a.mp4"}},
        {"type": "extract", "source": {"kind": "media", "name": "a.mp4"}, "startSec": 5, "endSec": 5},
        {"type": "extract", "source": {"kind": "media", "name": "a.mp4"}, "startSec": 1},
        {"type": "extract", "source": {"kind": "media", "name": "../a.mp4"}, "startSec": 1, "endSec": 2},
        {"type": "extract", "source": {"kind": "drive", "fileId": "abcdefghij"}, "startSec": 1, "endSec": 2},
        {"type": "extract", "source": {"kind": "drive", "fileId": "a/../b", "accessToken": "t"}, "startSec": 1, "endSec": 2},
        {"type": "extract", "startSec": 1, "endSec": 2},
        {"type": "splice", "parts": [{"source": {"kind": "media", "name": "a.mp4"}}]},
        {"type": "extract", "source": {"kind": "media", "name": "a.mp4"}, "startSec": 0, "endSec": 1, "outputName": ".."},
        {"type": "bogus"},
    ],
)
def test_create_rejects_invalid_requests(client, queued, body):
    assert client.post(JOBS, json=body).status_code == 422
    assert queued == []


def test_remote_source_must_be_public(client, queued):
    resp = client.post(JOBS, json={"type": "extract", "source": {"kind": "remote", "url": "http://127.0.0.1:9/x.mp4"},
                                   "startSec": 0, "endSec": 1})
    assert resp.status_code == 422
    assert "non-public" in resp.json()["detail"]


def test_rife_needs_server_command_and_rejects_boomerang(client, queued, monkeypatch):
    body = {"type": "rife", "source": {"kind": "media", "name": "a.mp4"}}
    monkeypatch.setattr(settings, "clip_stacker_rife_command", None)
    assert client.post(JOBS, json=body).status_code == 501
    monkeypatch.setattr(settings, "clip_stacker_rife_command", "rife {input} {output}")
    assert client.post(JOBS, json={**body, "params": {"mode": "boomerang"}}).status_code == 422
    assert client.post(JOBS, json={**body, "params": {"multiplier": 3}}).status_code == 422
    assert client.post(JOBS, json=body).status_code == 202


def test_auth_token_enforced_when_configured(client, queued, monkeypatch):
    monkeypatch.setattr(settings, "clip_stacker_auth_token", "s3cret")
    body = {"type": "extract", "source": {"kind": "media", "name": "a.mp4"}, "startSec": 0, "endSec": 1}
    assert client.post(JOBS, json=body).status_code == 401
    assert client.post(JOBS, json=body, headers={"Authorization": "Bearer nope"}).status_code == 401
    ok = client.post(JOBS, json=body, headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 202
    job_id = ok.json()["jobId"]
    assert client.get(f"{JOBS}/{job_id}").status_code == 401
    assert client.get(f"{JOBS}/{job_id}", headers={"Authorization": "s3cret"}).status_code == 200
    assert client.post(f"{JOBS}/{job_id}/cancel").status_code == 401
    # Preflight never carries credentials.
    assert client.options(JOBS).status_code == 204


def test_get_and_cancel_unknown_or_malformed_ids(client):
    assert client.get(f"{JOBS}/{'0' * 32}").status_code == 404
    assert client.get(f"{JOBS}/not-a-job-id").status_code == 400
    assert client.post(f"{JOBS}/{'0' * 32}/cancel").status_code == 404


# ---------- extract ----------
def test_extract_keyframe_aligned_uses_stream_copy(client, queued, media, temp_files_dir):
    job_id, secrets = _submit(client, queued, {
        "type": "extract", "source": {"kind": "media", "name": "src.mp4"},
        "startSec": 2, "endSec": 6, "outputName": "seg one.mp4",
    })
    job = _run(client, job_id, secrets)
    assert job["status"] == "succeeded", job
    result = job["result"]
    assert result["method"] == "copy"
    assert abs(result["durationSec"] - 4) <= jobs.COPY_TOLERANCE_SEC
    assert (result["width"], result["height"]) == (320, 240)
    assert result["name"] == "seg_one.mp4"
    assert result["url"] == f"{str(settings.static_base_url).rstrip('/')}/clip-stacker/media/seg_one.mp4"
    assert (media / "seg_one.mp4").is_file()
    job_dir = Path(temp_files_dir) / "clip-stacker" / "jobs" / job_id
    assert (job_dir / "job.json").is_file()
    assert not (job_dir / "work").exists()


def test_extract_off_keyframe_reencodes_to_exact_length(client, queued, media):
    job_id, secrets = _submit(client, queued, {
        "type": "extract", "source": {"kind": "media", "name": "src.mp4"}, "startSec": 3.04, "endSec": 6.04,
    })
    job = _run(client, job_id, secrets)
    assert job["status"] == "succeeded", job
    assert job["result"]["method"] == "reencode"
    assert abs(job["result"]["durationSec"] - 3.0) <= 0.1
    assert abs(_duration(media / job["result"]["name"]) - 3.0) <= 0.1


def test_extract_outputs_never_overwrite_existing_media(client, queued, media):
    (media / "taken.mp4").write_bytes(b"keep me")
    job_id, secrets = _submit(client, queued, {
        "type": "extract", "source": {"kind": "media", "name": "src.mp4"},
        "startSec": 0, "endSec": 2, "outputName": "taken.mp4",
    })
    job = _run(client, job_id, secrets)
    assert job["status"] == "succeeded", job
    assert (media / "taken.mp4").read_bytes() == b"keep me"
    assert job["result"]["name"] == f"taken-{job_id[:8]}.mp4"


def test_extract_past_end_fails_without_output(client, queued, media):
    job_id, secrets = _submit(client, queued, {
        "type": "extract", "source": {"kind": "media", "name": "src.mp4"}, "startSec": 8, "endSec": 14,
    })
    job = _run(client, job_id, secrets)
    assert job["status"] == "failed"
    assert "past the end" in job["error"]
    assert sorted(p.name for p in media.iterdir()) == ["src.mp4"]


def test_extract_missing_media_fails(client, queued, media):
    job_id, secrets = _submit(client, queued, {
        "type": "extract", "source": {"kind": "media", "name": "nope.mp4"}, "startSec": 0, "endSec": 1,
    })
    job = _run(client, job_id, secrets)
    assert job["status"] == "failed" and "not found" in job["error"]


def test_own_media_url_is_read_in_place(client, queued, media):
    url = f"{str(settings.static_base_url).rstrip('/')}/clip-stacker/media/src.mp4"
    job_id, secrets = _submit(client, queued, {
        "type": "extract", "source": {"kind": "remote", "url": url}, "startSec": 0, "endSec": 2,
    })
    assert _run(client, job_id, secrets)["status"] == "succeeded"


def test_result_can_be_listed_and_deleted_via_media_routes(client, queued, media):
    job_id, secrets = _submit(client, queued, {
        "type": "extract", "source": {"kind": "media", "name": "src.mp4"}, "startSec": 0, "endSec": 2,
    })
    name = _run(client, job_id, secrets)["result"]["name"]
    listed = client.get("/webhook/clip-stacker/media").json()["media"]
    assert name in [m["name"] for m in listed]
    assert client.delete(f"/webhook/clip-stacker/media/{name}").status_code == 200
    assert not (media / name).exists()


# ---------- Drive / remote download ----------
class _Source(BaseHTTPRequestHandler):
    seen_auth: list = []
    payload: bytes = b""

    def log_message(self, *args):
        pass

    def do_GET(self):
        _Source.seen_auth.append((self.headers.get("Host", "").split(":")[0], self.headers.get("Authorization")))
        if self.path.startswith("/redirect"):
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{self.server.server_port}/file")
            self.end_headers()
            return
        if self.headers.get("Authorization") != "Bearer drive-token" and self.path.startswith("/drive/"):
            self.send_response(401)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(_Source.payload)))
        self.end_headers()
        self.wfile.write(_Source.payload)


@pytest.fixture
def source_server(media, monkeypatch):
    _Source.seen_auth = []
    _Source.payload = (media / "src.mp4").read_bytes()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Source)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(jobs, "DRIVE_API_BASE", f"http://127.0.0.1:{server.server_port}")

    async def allow(url):
        return None

    monkeypatch.setattr(jobs, "_assert_public_url", allow)
    yield server
    server.shutdown()


def test_drive_source_downloads_with_token_and_never_persists_it(client, queued, media, source_server, temp_files_dir):
    job_id, secrets = _submit(client, queued, {
        "type": "extract",
        "source": {"kind": "drive", "fileId": "driveFile123", "accessToken": "drive-token", "name": "big.mp4"},
        "startSec": 2, "endSec": 5,
    })
    assert secrets == {"driveFile123": "drive-token"}
    job = _run(client, job_id, secrets)
    assert job["status"] == "succeeded", job
    assert ("127.0.0.1", "Bearer drive-token") in _Source.seen_auth
    job_dir = Path(temp_files_dir) / "clip-stacker" / "jobs" / job_id
    for f in job_dir.iterdir():
        assert "drive-token" not in f.read_text(errors="ignore")
    assert not (job_dir / "work").exists()  # the multi-GB download is gone


def test_splice_keeps_every_drive_token_out_of_the_job_record(client, queued, media, source_server, temp_files_dir):
    job_id, secrets = _submit(client, queued, {
        "type": "splice",
        "parts": [
            {"source": {"kind": "drive", "fileId": "driveFile123", "accessToken": "drive-token"}, "startSec": 0, "endSec": 2},
            {"source": {"kind": "drive", "fileId": "driveFile456", "accessToken": "other-token"}, "startSec": 4, "endSec": 6},
            {"source": {"kind": "media", "name": "src.mp4"}, "startSec": 8, "endSec": 10},
        ],
    })
    assert secrets == {"driveFile123": "drive-token", "driveFile456": "other-token"}
    stored = (Path(temp_files_dir) / "clip-stacker" / "jobs" / job_id / "job.json").read_text()
    assert "drive-token" not in stored and "other-token" not in stored
    assert "driveFile123" in stored  # the reference stays; only the credential is dropped


def test_drive_bad_token_reports_clear_error(client, queued, media, source_server):
    job_id, secrets = _submit(client, queued, {
        "type": "extract", "source": {"kind": "drive", "fileId": "driveFile123", "accessToken": "wrong"},
        "startSec": 2, "endSec": 5,
    })
    job = _run(client, job_id, secrets)
    assert job["status"] == "failed" and "token may have expired" in job["error"]


def test_auth_header_is_not_forwarded_across_hosts(client, queued, media, source_server, monkeypatch):
    monkeypatch.setattr(jobs, "DRIVE_API_BASE", f"http://127.0.0.1:{source_server.server_port}/redirect")
    # /drive/... path is appended after the base, so serve it from the redirect handler.
    job_id, secrets = _submit(client, queued, {
        "type": "extract", "source": {"kind": "drive", "fileId": "driveFile123", "accessToken": "drive-token"},
        "startSec": 2, "endSec": 5,
    })
    job = _run(client, job_id, secrets)
    assert job["status"] == "succeeded", job
    by_host = {host: auth for host, auth in _Source.seen_auth}
    assert by_host["127.0.0.1"] == "Bearer drive-token"
    assert by_host["localhost"] is None


# ---------- splice ----------
def test_splice_copy_parts_with_matching_streams(client, queued, media):
    job_id, secrets = _submit(client, queued, {
        "type": "splice",
        "parts": [
            {"source": {"kind": "media", "name": "src.mp4"}, "startSec": 0, "endSec": 2},
            {"source": {"kind": "media", "name": "src.mp4"}, "startSec": 4, "endSec": 6},
            {"source": {"kind": "media", "name": "src.mp4"}, "startSec": 8, "endSec": 10},
        ],
    })
    job = _run(client, job_id, secrets)
    assert job["status"] == "succeeded", job
    assert job["result"]["method"] == "copy"
    assert abs(job["result"]["durationSec"] - 6) <= 0.5


def test_splice_normalizes_parts_that_differ(client, queued, media):
    _make_clip(media / "other.mp4", 3, size="160x120", fps=30)
    job_id, secrets = _submit(client, queued, {
        "type": "splice",
        "parts": [
            {"source": {"kind": "media", "name": "src.mp4"}, "startSec": 0, "endSec": 2},
            {"source": {"kind": "media", "name": "other.mp4"}},
        ],
    })
    job = _run(client, job_id, secrets)
    assert job["status"] == "succeeded", job
    assert job["result"]["method"] == "reencode"
    assert (job["result"]["width"], job["result"]["height"]) == (320, 240)
    assert abs(job["result"]["durationSec"] - 5) <= 0.5


# ---------- rife ----------
def test_rife_runs_configured_command_and_preserves_length_and_audio(client, queued, media, monkeypatch):
    monkeypatch.setattr(
        settings, "clip_stacker_rife_command",
        "ffmpeg -v error -nostdin -y -i {input} -an -r 50 -c:v libx264 {output}",
    )
    job_id, secrets = _submit(client, queued, {
        "type": "rife", "source": {"kind": "media", "name": "src.mp4"}, "startSec": 2, "endSec": 5,
        "params": {"multiplier": 2},
    })
    job = _run(client, job_id, secrets)
    assert job["status"] == "succeeded", job
    result = job["result"]
    assert result["method"] == "rife"
    assert abs(result["fps"] - 50) < 1
    assert abs(result["durationSec"] - 3) <= 0.3
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(media / result["name"])],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert sorted(probe) == ["audio", "video"]


def test_rife_output_with_wrong_length_is_rejected(client, queued, media, monkeypatch):
    monkeypatch.setattr(
        settings, "clip_stacker_rife_command",
        "ffmpeg -v error -nostdin -y -i {input} -t 1 -an -c:v libx264 {output}",
    )
    job_id, secrets = _submit(client, queued, {
        "type": "rife", "source": {"kind": "media", "name": "src.mp4"}, "startSec": 2, "endSec": 5,
    })
    job = _run(client, job_id, secrets)
    assert job["status"] == "failed" and "length mismatch" in job["error"]
    assert sorted(p.name for p in media.iterdir()) == ["src.mp4"]


# ---------- lifecycle ----------
def test_cancel_queued_job_and_double_cancel(client, queued, media):
    job_id, _ = _submit(client, queued, {
        "type": "extract", "source": {"kind": "media", "name": "src.mp4"}, "startSec": 0, "endSec": 2,
    })
    resp = client.post(f"{JOBS}/{job_id}/cancel")
    assert resp.status_code == 200 and resp.json()["status"] == "cancelled"
    assert client.post(f"{JOBS}/{job_id}/cancel").status_code == 409
    # A worker that starts late must not resurrect the job.
    assert _run(client, job_id, {})["status"] == "cancelled"


def test_cancel_running_job_stops_ffmpeg(client, queued, media, monkeypatch):
    started = {}

    async def slow_execute(job_id, req, secrets, work, log_path):
        started["proc"] = await asyncio.create_subprocess_exec("sleep", "30")
        try:
            await started["proc"].wait()
        except asyncio.CancelledError:
            started["proc"].kill()
            raise

    monkeypatch.setattr(jobs, "_execute", slow_execute)
    job_id, _ = _submit(client, queued, {
        "type": "extract", "source": {"kind": "media", "name": "src.mp4"}, "startSec": 0, "endSec": 2,
    })

    async def scenario():
        task = asyncio.create_task(jobs._run_job(job_id, {}))
        jobs._tasks[job_id] = task
        for _ in range(100):
            if "proc" in started:
                break
            await asyncio.sleep(0.05)
        cancelled = await jobs.cancel_job(job_id, None)
        with pytest.raises(asyncio.CancelledError):
            await task
        await started["proc"].wait()
        return cancelled

    assert asyncio.run(scenario())["status"] == "cancelled"
    assert client.get(f"{JOBS}/{job_id}").json()["status"] == "cancelled"
    assert job_id not in jobs._tasks


def test_orphaned_running_job_is_reported_failed(client, temp_files_dir):
    job_id = "a" * 32
    jobs._write_job({
        "id": job_id, "type": "extract", "status": "running", "stage": "cutting", "progress": 0.5,
        "createdAt": "x", "updatedAt": "x", "request": {}, "result": None, "error": None,
    })
    job = client.get(f"{JOBS}/{job_id}").json()
    assert job["status"] == "failed" and "restart" in job["error"]


def test_old_job_folders_are_pruned_on_create(client, queued, temp_files_dir, monkeypatch):
    old = Path(temp_files_dir) / "clip-stacker" / "jobs" / ("b" * 32)
    old.mkdir(parents=True)
    (old / "job.json").write_text(json.dumps({"id": "b" * 32}))
    import os, time
    stale = time.time() - 30 * 86400
    os.utime(old / "job.json", (stale, stale))
    resp = client.post(JOBS, json={"type": "extract", "source": {"kind": "media", "name": "a.mp4"}, "startSec": 0, "endSec": 1})
    assert resp.status_code == 202
    assert not old.exists()
