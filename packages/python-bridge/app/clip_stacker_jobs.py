"""clip-stacker processing jobs.

    POST /webhook/clip-stacker/jobs                 create a job (202)
    GET  /webhook/clip-stacker/jobs/{jobId}         poll status / result
    POST /webhook/clip-stacker/jobs/{jobId}/cancel  cancel a queued or running job

Job types:
  extract  cut [startSec, endSec] out of one source (ffmpeg -ss/-to -c copy)
  splice   concatenate an ordered list of parts (each optionally a sub-range)
  rife     extract (optional) then run the configured RIFE command

A job lives in files/clip-stacker/jobs/{jobId}/ (job.json + ffmpeg.log; the
download/scratch folder `work/` is removed when the job ends). The finished
file is moved into files/clip-stacker/media/ so the existing media
list/delete routes (webhooks.py) manage it and clip-stacker pulls it back
through the returned public URL.

Length-preserving: extract/rife results are measured with ffprobe and a job
fails rather than reporting a result whose duration is off the requested
length by more than the tolerance, so a replacement never shifts the timeline.
"""
import asyncio
import hmac
import ipaddress
import json
import logging
import re
import shlex
import shutil
import socket
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel, Field, model_validator

from .config import settings
from .ftp_client import ftp_client

logger = logging.getLogger(__name__)

jobs_router = APIRouter(prefix="/webhook/clip-stacker/jobs", tags=["clip-stacker-jobs"])

JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
DRIVE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,200}$")
DRIVE_API_BASE = "https://www.googleapis.com"
ACTIVE_STATUSES = ("queued", "running")
OUTPUT_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4a", ".mp3", ".wav", ".flac", ".ogg", ".aac"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm"}
MIN_SEGMENT_SEC = 0.05
# Stream-copy output keeps B-frame delay / packet granularity, so it can run a few
# frames long; clip-stacker trims the surplus on replace.
COPY_TOLERANCE_SEC = 0.25
MAX_REDIRECTS = 5


class JobFailed(Exception):
    """A job error whose message is safe to show to the user."""


# ====================== Request models ======================
class JobSource(BaseModel):
    kind: Literal["drive", "remote", "media"]
    fileId: Optional[str] = Field(None, max_length=256)
    accessToken: Optional[str] = Field(None, max_length=4096, repr=False)
    url: Optional[str] = Field(None, max_length=4096)
    name: Optional[str] = Field(None, max_length=255)

    @model_validator(mode="after")
    def _check_kind(self):
        if self.kind == "drive":
            if not self.fileId or not DRIVE_ID_RE.match(self.fileId):
                raise ValueError("drive source needs a valid 'fileId'")
            if not self.accessToken:
                raise ValueError("drive source needs an 'accessToken' (Google OAuth, drive.readonly)")
        elif self.kind == "remote":
            if not self.url:
                raise ValueError("remote source needs 'url'")
        elif not self.name or "/" in self.name or "\\" in self.name or ".." in self.name:
            raise ValueError("media source needs a plain 'name' from clip-stacker/media")
        return self


class JobPart(BaseModel):
    source: JobSource
    startSec: Optional[float] = Field(None, ge=0)
    endSec: Optional[float] = Field(None, gt=0)

    @model_validator(mode="after")
    def _check_range(self):
        if (self.startSec is None) != (self.endSec is None):
            raise ValueError("give both startSec and endSec, or neither")
        if self.startSec is not None and self.endSec - self.startSec < MIN_SEGMENT_SEC:
            raise ValueError("endSec must be greater than startSec")
        return self


class JobRequest(BaseModel):
    type: Literal["extract", "splice", "rife"]
    source: Optional[JobSource] = None
    startSec: Optional[float] = Field(None, ge=0)
    endSec: Optional[float] = Field(None, gt=0)
    parts: Optional[list[JobPart]] = Field(None, max_length=50)
    outputName: Optional[str] = Field(None, max_length=200)
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_type(self):
        if self.outputName is not None and not any(c.isalnum() for c in self.outputName):
            raise ValueError("outputName needs at least one letter or digit")
        has_range = self.startSec is not None or self.endSec is not None
        if has_range and (self.startSec is None or self.endSec is None):
            raise ValueError("give both startSec and endSec, or neither")
        if has_range and self.endSec - self.startSec < MIN_SEGMENT_SEC:
            raise ValueError("endSec must be greater than startSec")
        if self.type in ("extract", "rife"):
            if self.source is None:
                raise ValueError(f"'{self.type}' jobs need a 'source'")
            if self.type == "extract" and not has_range:
                raise ValueError("'extract' jobs need startSec and endSec")
        if self.type == "rife":
            if self.params.get("multiplier", 2) not in (2, 4):
                raise ValueError("params.multiplier must be 2 or 4")
            if self.params.get("mode", "interpolation") != "interpolation":
                raise ValueError(
                    "only params.mode 'interpolation' is length-preserving; "
                    "'boomerang' changes the clip duration"
                )
        if self.type == "splice" and (not self.parts or len(self.parts) < 2):
            raise ValueError("'splice' jobs need at least two 'parts'")
        return self


# ====================== Paths, auth, job documents ======================
def _clip_stacker_dir() -> Path:
    return Path(settings.files_dir) / "clip-stacker"


def _media_dir() -> Path:
    return _clip_stacker_dir() / "media"


def _jobs_dir() -> Path:
    return _clip_stacker_dir() / "jobs"


def _job_dir(job_id: str) -> Path:
    if not JOB_ID_RE.match(job_id):
        raise HTTPException(status_code=400, detail="Invalid job id")
    return _jobs_dir() / job_id


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_auth(authorization: Optional[str]) -> None:
    """Enforce CLIP_STACKER_AUTH_TOKEN when configured (open otherwise, like the other clip-stacker routes)."""
    expected = settings.clip_stacker_auth_token
    if not expected:
        return
    supplied = (authorization or "").strip()
    if supplied.lower().startswith("bearer "):
        supplied = supplied[7:].strip()
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Invalid or missing Authorization bearer token")


def _read_job(job_id: str) -> dict:
    path = _job_dir(job_id) / "job.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Unreadable job document %s: %s", path, exc)
        raise HTTPException(status_code=500, detail="Job record is unreadable")


def _write_job(job: dict) -> None:
    job_dir = _job_dir(job["id"])
    job_dir.mkdir(parents=True, exist_ok=True)
    job["updatedAt"] = _now_iso()
    tmp = job_dir / "job.json.tmp"
    tmp.write_text(json.dumps(job, indent=2), encoding="utf-8")
    tmp.replace(job_dir / "job.json")


def _update_job(job_id: str, **fields: Any) -> dict:
    job = _read_job(job_id)
    # A cancelled job stays cancelled even if the worker races a late update in.
    if job["status"] == "cancelled" and fields.get("status") not in (None, "cancelled"):
        return job
    job.update(fields)
    _write_job(job)
    return job


def _public_job(job: dict) -> dict:
    """The document returned to clients (everything in job.json is already token-free)."""
    return {
        "jobId": job["id"],
        "type": job["type"],
        "status": job["status"],
        "stage": job.get("stage"),
        "progress": job.get("progress", 0.0),
        "createdAt": job["createdAt"],
        "updatedAt": job["updatedAt"],
        "result": job.get("result"),
        "error": job.get("error"),
    }


def _prune_old_jobs() -> None:
    cutoff = time.time() - settings.clip_stacker_job_retention_days * 86400
    root = _jobs_dir()
    if not root.is_dir():
        return
    for entry in root.iterdir():
        try:
            if not (entry.is_dir() and JOB_ID_RE.match(entry.name)):
                continue
            if entry.name in _tasks:
                continue
            marker = entry / "job.json"
            if (marker if marker.exists() else entry).stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            continue


# ====================== Source acquisition ======================
def _safe_media_name(name: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "._-" else "_" for c in name).lstrip(".")
    if not cleaned:
        raise JobFailed("Invalid media file name")
    return cleaned


def _local_media_path(name: str) -> Path:
    media_dir = _media_dir()
    path = media_dir / name
    try:
        inside = path.resolve().is_relative_to(media_dir.resolve())
    except OSError:
        inside = False
    if not inside or not path.is_file():
        raise JobFailed(f"Media file '{name}' not found in clip-stacker/media")
    return path


def _own_media_name(url: str) -> Optional[str]:
    """If `url` is one of our own clip-stacker/media public URLs, return the file name."""
    prefix = str(settings.static_base_url).rstrip("/") + "/clip-stacker/media/"
    if url.startswith(prefix):
        name = url[len(prefix):].split("?", 1)[0]
        if name and "/" not in name and ".." not in name:
            return name
    return None


async def _assert_public_url(url: str) -> None:
    """Refuse non-http(s) URLs and hosts that resolve to private/loopback/link-local space."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise JobFailed("Remote source must be an http(s) URL")
    if parsed.username or parsed.password:
        raise JobFailed("Remote source URL must not contain credentials")
    try:
        infos = await asyncio.to_thread(
            socket.getaddrinfo, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
        )
    except socket.gaierror:
        raise JobFailed(f"Could not resolve {parsed.hostname}")
    for info in infos:
        if not ipaddress.ip_address(info[4][0].split("%")[0]).is_global:
            raise JobFailed("Remote source resolves to a non-public address")


async def _download(url: str, dest: Path, headers: Optional[dict], on_progress) -> None:
    """Stream `url` to `dest`. `headers` (e.g. Drive bearer) go only to the original host."""
    limit = settings.clip_stacker_max_source_mb * 1024 * 1024
    origin_host = urlparse(url).hostname
    current = url
    timeout = httpx.Timeout(30.0, read=300.0)
    async with httpx.AsyncClient(follow_redirects=False, timeout=timeout) as client:
        for _ in range(MAX_REDIRECTS + 1):
            await _assert_public_url(current)
            send = headers if urlparse(current).hostname == origin_host else None
            async with client.stream("GET", current, headers=send) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("location")
                    if not location:
                        raise JobFailed("Source redirected without a location")
                    current = urljoin(current, location)
                    continue
                if resp.status_code in (401, 403):
                    raise JobFailed(
                        f"Source refused access (HTTP {resp.status_code}) — for Drive, the token may have expired"
                    )
                if resp.status_code == 404:
                    raise JobFailed("Source not found (HTTP 404)")
                if resp.status_code >= 400:
                    raise JobFailed(f"Source download failed (HTTP {resp.status_code})")
                total = int(resp.headers.get("content-length") or 0)
                if total > limit:
                    raise JobFailed(f"Source is {total // (1024 * 1024)} MB; the limit is {limit // (1024 * 1024)} MB")
                written = 0
                with dest.open("wb") as fh:
                    async for chunk in resp.aiter_bytes(1 << 20):
                        written += len(chunk)
                        if written > limit:
                            raise JobFailed("Source exceeds the configured size limit")
                        await asyncio.to_thread(fh.write, chunk)
                        if total:
                            on_progress(written / total)
                return
    raise JobFailed("Too many redirects fetching the source")


async def _materialize(source: JobSource, work: Path, index: int, on_progress) -> Path:
    """Return a local path for `source` (existing media is used in place, never copied)."""
    if source.kind == "media":
        return _local_media_path(source.name)
    if source.kind == "remote":
        own = _own_media_name(source.url)
        if own:
            return _local_media_path(own)
        url, headers = source.url, None
        hint = Path(urlparse(source.url).path).suffix
    else:
        url = f"{DRIVE_API_BASE}/drive/v3/files/{source.fileId}?alt=media&supportsAllDrives=true"
        headers = {"Authorization": f"Bearer {source.accessToken}"}
        hint = Path(source.name or "").suffix
    suffix = hint.lower() if re.fullmatch(r"\.[a-z0-9]{1,5}", hint.lower() or "") else ""
    dest = work / f"source-{index}{suffix}"
    await _download(url, dest, headers, on_progress)
    return dest


# ====================== ffmpeg / ffprobe ======================
async def _run(cmd: list[str], log_path: Path, *, quiet: bool = False) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await proc.communicate()
    except asyncio.CancelledError:
        proc.kill()
        await proc.wait()
        raise
    text = out.decode(errors="replace")
    if proc.returncode != 0 or not quiet:
        try:
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(f"$ {' '.join(shlex.quote(c) for c in cmd)}\n{text[-8000:]}\n[exit {proc.returncode}]\n\n")
        except OSError:
            pass
    return proc.returncode, text


def _fps(rate: Optional[str]) -> Optional[float]:
    try:
        num, _, den = (rate or "").partition("/")
        value = float(num) / float(den or 1)
        return value if value > 0 else None
    except (ValueError, ZeroDivisionError):
        return None


async def _probe(path: Path, log_path: Path) -> dict:
    code, text = await _run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        log_path,
        quiet=True,
    )
    if code != 0:
        raise JobFailed("Source is not a readable media file")
    # The log helper merged stderr into stdout; the JSON object is the last block.
    try:
        data = json.loads(text[text.index("{"):])
    except (ValueError, json.JSONDecodeError):
        raise JobFailed("Could not read media information")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic") != 1), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    try:
        duration = float(data.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        duration = 0.0
    return {
        "duration": duration,
        "hasVideo": video is not None,
        "hasAudio": audio is not None,
        "width": video.get("width") if video else None,
        "height": video.get("height") if video else None,
        "fps": _fps(video.get("avg_frame_rate")) if video else None,
        "vcodec": video.get("codec_name") if video else None,
        "pixFmt": video.get("pix_fmt") if video else None,
        "acodec": audio.get("codec_name") if audio else None,
        "sampleRate": audio.get("sample_rate") if audio else None,
        "channels": audio.get("channels") if audio else None,
    }


async def _starts_on_keyframe(src: Path, start: float, fps: Optional[float], log_path: Path) -> bool:
    """True when a video packet at `start` is a keyframe (stream copy then begins exactly there)."""
    if start <= 0:
        return True
    half_frame = 0.5 / (fps or 25.0)
    code, text = await _run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-read_intervals", f"{max(0.0, start - 0.001):.3f}%+4",
            "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0", str(src),
        ],
        log_path,
        quiet=True,
    )
    if code != 0:
        return False
    best: Optional[tuple[float, str]] = None
    for line in text.splitlines():
        pts, _, flags = line.partition(",")
        try:
            gap = abs(float(pts) - start)
        except ValueError:
            continue
        if best is None or gap < best[0]:
            best = (gap, flags)
    return best is not None and best[0] <= half_frame and "K" in best[1]


def _pick_ext(requested: Optional[str], source_name: str, has_video: bool, *, copy: bool) -> str:
    """Output container: the requested one if it suits the media, else the source's (copy only), else mp4/m4a."""
    def suits(ext: str) -> bool:
        return ext in OUTPUT_EXTS and has_video == (ext in VIDEO_EXTS)

    for candidate in (Path(requested or "").suffix.lower(), Path(source_name).suffix.lower() if copy else ""):
        if suits(candidate):
            return candidate
    return ".mp4" if has_video else ".m4a"


AUDIO_ENCODERS = {".mp3": "libmp3lame", ".wav": "pcm_s16le", ".flac": "flac", ".ogg": "libvorbis"}


def _within(actual: float, wanted: float, tol: float) -> bool:
    return abs(actual - wanted) <= tol


async def _cut_segment(
    src: Path,
    start: float,
    end: float,
    out_stem: Path,
    *,
    out_name_hint: Optional[str],
    source_name: str,
    log_path: Path,
) -> tuple[Path, dict, str]:
    """Cut [start, end] from `src`. Returns (file, probe-of-file, "copy" | "reencode")."""
    wanted = end - start
    info = await _probe(src, log_path)
    tol = max(0.08, 2.0 / (info["fps"] or 25.0))
    if info["duration"] and start >= info["duration"]:
        raise JobFailed(f"startSec {start:g}s is past the end of the source ({info['duration']:.2f}s)")
    if info["duration"] and end > info["duration"] + tol:
        raise JobFailed(f"endSec {end:g}s is past the end of the source ({info['duration']:.2f}s)")

    exact_start = (not info["hasVideo"]) or await _starts_on_keyframe(src, start, info["fps"], log_path)
    if exact_start:
        ext = _pick_ext(out_name_hint, source_name, info["hasVideo"], copy=True)
        out = out_stem.with_suffix(ext)
        code, _ = await _run(
            [
                "ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-loglevel", "error", "-y",
                "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src),
                "-map", "0:v?", "-map", "0:a?", "-sn", "-dn",
                "-c", "copy", "-avoid_negative_ts", "make_zero", str(out),
            ],
            log_path,
        )
        if code == 0 and out.is_file():
            cut = await _probe(out, log_path)
            if _within(cut["duration"], wanted, COPY_TOLERANCE_SEC):
                return out, cut, "copy"
        out.unlink(missing_ok=True)

    # Stream copy would start off-keyframe (pre-roll) or missed the length: cut exactly.
    ext = _pick_ext(out_name_hint, source_name, info["hasVideo"], copy=False)
    out = out_stem.with_suffix(ext)
    args = [
        "ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-loglevel", "error", "-y",
        "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{wanted:.3f}",
        "-map", "0:v?", "-map", "0:a?", "-sn", "-dn",
    ]
    if info["hasVideo"]:
        args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-pix_fmt", "yuv420p"]
    args += ["-c:a", AUDIO_ENCODERS.get(ext, "aac")]
    if AUDIO_ENCODERS.get(ext, "aac") == "aac":
        args += ["-b:a", "192k"]
    if ext in (".mp4", ".mov", ".m4a"):
        args += ["-movflags", "+faststart"]
    code, _ = await _run(args + [str(out)], log_path)
    if code != 0 or not out.is_file():
        raise JobFailed("ffmpeg could not cut the segment (see ffmpeg.log)")
    cut = await _probe(out, log_path)
    if not _within(cut["duration"], wanted, tol):
        raise JobFailed(
            f"Cut segment is {cut['duration']:.3f}s but {wanted:.3f}s was requested — refusing a length mismatch"
        )
    return out, cut, "reencode"


# ====================== Job execution ======================
def _signature(info: dict) -> tuple:
    return (
        info["vcodec"], info["width"], info["height"], info["pixFmt"],
        round(info["fps"] or 0, 2), info["acodec"], info["sampleRate"], info["channels"],
    )


async def _normalize_to(part: Path, ref: dict, keep_audio: bool, has_audio: bool, out: Path, log_path: Path) -> None:
    w, h, fps = ref["width"], ref["height"], ref["fps"] or 30
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps:g},format=yuv420p"
    )
    args = ["ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-loglevel", "error", "-y", "-i", str(part)]
    if keep_audio and not has_audio:
        args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-map", "0:v:0", "-map", "1:a:0", "-shortest"]
    else:
        args += ["-map", "0:v:0"] + (["-map", "0:a:0"] if keep_audio else [])
    args += ["-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "16"]
    args += ["-c:a", "aac", "-ar", "48000", "-ac", "2"] if keep_audio else ["-an"]
    args += ["-movflags", "+faststart", str(out)]
    code, _ = await _run(args, log_path)
    if code != 0 or not out.is_file():
        raise JobFailed("ffmpeg could not normalize a splice part (see ffmpeg.log)")


def _concat_line(path: Path) -> str:
    return "file '" + str(path.resolve()).replace("'", "'\\''") + "'\n"


async def _splice(job_id: str, req: dict, secrets: dict, work: Path, log_path: Path) -> tuple[Path, dict, str]:
    parts = req["parts"]
    cuts: list[tuple[Path, dict]] = []
    methods = set()
    for i, raw in enumerate(parts):
        source = _hydrate_source(raw["source"], secrets)
        start, end = raw.get("startSec"), raw.get("endSec")
        _progress(job_id, "fetching", 0.05 + 0.5 * i / len(parts))
        src = await _materialize(source, work, i, lambda f: None)
        if start is None:
            info = await _probe(src, log_path)
            cuts.append((src, info))
            continue
        out, info, method = await _cut_segment(
            src, start, end, work / f"part-{i}",
            out_name_hint=None, source_name=source.name or src.name, log_path=log_path,
        )
        methods.add(method)
        cuts.append((out, info))

    _progress(job_id, "joining", 0.6)
    if any(not info["hasVideo"] for _, info in cuts) and not all(not info["hasVideo"] for _, info in cuts):
        raise JobFailed("Cannot splice audio-only parts with video parts")
    audio_only = not cuts[0][1]["hasVideo"]
    method = "copy"
    if len({_signature(info) for _, info in cuts}) > 1:
        if audio_only:
            raise JobFailed("Audio parts differ in codec/sample rate; re-export them to match")
        keep_audio = all(info["hasAudio"] for _, info in cuts)
        ref = cuts[0][1]
        normalized = []
        for i, (path, info) in enumerate(cuts):
            out = work / f"norm-{i}.mp4"
            await _normalize_to(path, ref, keep_audio, info["hasAudio"], out, log_path)
            normalized.append((out, await _probe(out, log_path)))
        cuts, method = normalized, "reencode"
    elif "reencode" in methods:
        method = "reencode"

    listing = work / "concat.txt"
    listing.write_text("".join(_concat_line(p) for p, _ in cuts), encoding="utf-8")
    ext = _pick_ext(req.get("outputName"), cuts[0][0].name, not audio_only, copy=True)
    out = work / f"splice{ext}"
    code, _ = await _run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-loglevel", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy",
            *( ["-movflags", "+faststart"] if ext in (".mp4", ".mov", ".m4a") else [] ),
            str(out),
        ],
        log_path,
    )
    if code != 0 or not out.is_file():
        raise JobFailed("ffmpeg could not concatenate the parts (see ffmpeg.log)")
    info = await _probe(out, log_path)
    total = sum(i["duration"] for _, i in cuts)
    if not _within(info["duration"], total, max(0.25, 0.1 * len(cuts))):
        raise JobFailed(f"Spliced file is {info['duration']:.2f}s but the parts add up to {total:.2f}s")
    return out, info, method


async def _rife(job_id: str, req: dict, src: Path, seg: Optional[Path], seg_info: dict, work: Path, log_path: Path):
    template = settings.clip_stacker_rife_command
    if not template:
        raise JobFailed("RIFE is not configured on this server (set CLIP_STACKER_RIFE_COMMAND)")
    params = req.get("params") or {}
    inp = seg or src
    raw_out = work / "rife-raw.mp4"
    cmd = [
        tok.format(input=str(inp), output=str(raw_out), multiplier=params.get("multiplier", 2), mode="interpolation")
        for tok in shlex.split(template)
    ]
    _progress(job_id, "interpolating", 0.6)
    code, _ = await _run(cmd, log_path)
    if code != 0 or not raw_out.is_file():
        raise JobFailed("The RIFE command failed (see ffmpeg.log)")
    raw_info = await _probe(raw_out, log_path)
    # Check the video itself before muxing: the segment's audio would otherwise pad a short result to length.
    tol = max(0.1, 2.0 / (raw_info["fps"] or 25.0))
    if not _within(raw_info["duration"], seg_info["duration"], tol):
        raise JobFailed(
            f"RIFE output is {raw_info['duration']:.3f}s but the input was {seg_info['duration']:.3f}s — refusing a length mismatch"
        )
    out = work / "rife.mp4"
    args = ["ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-loglevel", "error", "-y", "-i", str(raw_out)]
    if seg_info["hasAudio"]:
        # RIFE tools usually drop audio; carry the segment's own track across, length-pinned.
        args += ["-i", str(inp), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-t", f"{seg_info['duration']:.3f}"]
    else:
        args += ["-map", "0:v:0", "-c:v", "copy"]
    code, _ = await _run(args + ["-movflags", "+faststart", str(out)], log_path)
    if code != 0 or not out.is_file():
        raise JobFailed("ffmpeg could not remux the RIFE output (see ffmpeg.log)")
    return out, await _probe(out, log_path)


def _progress(job_id: str, stage: str, progress: float) -> None:
    try:
        _update_job(job_id, stage=stage, progress=round(progress, 3))
    except HTTPException:
        pass


def _hydrate_source(raw: dict, secrets: dict) -> JobSource:
    """Rebuild a source from job.json, re-attaching the Drive token held only in memory."""
    if raw.get("kind") == "drive":
        raw = {**raw, "accessToken": secrets.get(raw.get("fileId"))}
        if not raw["accessToken"]:
            raise JobFailed("Drive access token is no longer available; submit the job again")
    return JobSource(**raw)


def _publish(out: Path, job_id: str, req: dict, fallback_stem: str) -> tuple[str, str]:
    """Move `out` into clip-stacker/media and return (file name, public URL)."""
    media_dir = _media_dir()
    media_dir.mkdir(parents=True, exist_ok=True)
    wanted = req.get("outputName")
    ext = out.suffix.lower()
    stem = Path(_safe_media_name(wanted)).stem if wanted else fallback_stem
    stem = _safe_media_name(stem)
    name = f"{stem}{ext}"
    if (media_dir / name).exists():
        # Never overwrite someone's media; the job id keeps the name unique and traceable.
        name = f"{stem}-{job_id[:8]}{ext}"
    shutil.move(str(out), str(media_dir / name))
    return name, f"{str(settings.static_base_url).rstrip('/')}/clip-stacker/media/{name}"


async def _execute(job_id: str, req: dict, secrets: dict, work: Path, log_path: Path) -> dict:
    kind = req["type"]
    if kind == "splice":
        out, info, method = await _splice(job_id, req, secrets, work, log_path)
        fallback_stem = f"splice_{job_id[:8]}"
    else:
        source = _hydrate_source(req["source"], secrets)
        label = source.name or Path(urlparse(source.url or "").path).name or source.fileId or "media"
        _progress(job_id, "fetching", 0.05)
        src = await _materialize(
            source, work, 0,
            lambda frac: _progress(job_id, "downloading", 0.05 + 0.45 * frac),
        )
        seg, seg_info, method = None, None, "copy"
        if req.get("startSec") is not None:
            _progress(job_id, "cutting", 0.55)
            seg, seg_info, method = await _cut_segment(
                src, req["startSec"], req["endSec"], work / "segment",
                out_name_hint=None if kind == "rife" else req.get("outputName"),
                source_name=label, log_path=log_path,
            )
        elif kind == "rife":
            seg_info = await _probe(src, log_path)
        if kind == "extract":
            out, info = seg, seg_info
        else:
            out, info = await _rife(job_id, req, src, seg, seg_info, work, log_path)
            method = "rife"
        start, end = req.get("startSec"), req.get("endSec")
        span = f"_{start:g}-{end:g}" if start is not None else ""
        fallback_stem = f"{_safe_media_name(Path(label).stem)}{span}_{'rife' if kind == 'rife' else 'seg'}_{job_id[:8]}"

    _progress(job_id, "finishing", 0.95)
    name, url = _publish(out, job_id, req, fallback_stem)
    try:
        await ftp_client.upload(_media_dir() / name, f"clip-stacker/media/{name}")
    except Exception as exc:  # same non-fatal mirror as the media upload route
        logger.warning("FTP upload failed for clip-stacker job output (non-fatal): %s", exc)
    return {
        "url": url,
        "name": name,
        "durationSec": round(info["duration"], 3),
        "width": info["width"],
        "height": info["height"],
        "fps": round(info["fps"], 3) if info["fps"] else None,
        "sizeBytes": (_media_dir() / name).stat().st_size,
        "method": method,
    }


_tasks: dict[str, "asyncio.Task[None]"] = {}
_slots: Optional[asyncio.Semaphore] = None
_slots_loop: Optional[asyncio.AbstractEventLoop] = None


def _get_slots() -> asyncio.Semaphore:
    global _slots, _slots_loop
    loop = asyncio.get_running_loop()
    if _slots is None or _slots_loop is not loop:
        _slots = asyncio.Semaphore(max(1, settings.clip_stacker_max_concurrent_jobs))
        _slots_loop = loop
    return _slots


async def _run_job(job_id: str, secrets: dict) -> None:
    job_dir = _job_dir(job_id)
    work = job_dir / "work"
    try:
        async with _get_slots():
            job = _update_job(job_id, status="running", stage="starting", progress=0.0)
            if job["status"] == "cancelled":
                return
            work.mkdir(parents=True, exist_ok=True)
            result = await _execute(job_id, job["request"], secrets, work, job_dir / "ffmpeg.log")
            _update_job(job_id, status="succeeded", stage="done", progress=1.0, result=result)
    except asyncio.CancelledError:
        _update_job(job_id, status="cancelled", stage="cancelled")
        raise
    except JobFailed as exc:
        _update_job(job_id, status="failed", stage="failed", error=str(exc))
    except Exception as exc:
        logger.exception("clip-stacker job %s crashed", job_id)
        _update_job(job_id, status="failed", stage="failed", error=f"Unexpected error: {type(exc).__name__}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
        _tasks.pop(job_id, None)


def _schedule(job_id: str, secrets: dict) -> None:
    _tasks[job_id] = asyncio.create_task(_run_job(job_id, secrets))


# ====================== Routes ======================
def _cors_preflight() -> Response:
    return Response(
        status_code=204,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS, HEAD",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
            "Access-Control-Max-Age": "86400",
        },
    )


@jobs_router.options("")
async def jobs_options():
    return _cors_preflight()


@jobs_router.options("/{job_id}")
async def job_options(job_id: str):
    return _cors_preflight()


@jobs_router.options("/{job_id}/cancel")
async def job_cancel_options(job_id: str):
    return _cors_preflight()


@jobs_router.post("", status_code=202, response_model=dict)
async def create_job(body: JobRequest, authorization: Optional[str] = Header(None)):
    """Create a job. Body: { type, source | parts, startSec?, endSec?, outputName?, params? }.

    Sources: {kind:"drive", fileId, accessToken, name?} (token is used for the
    download only and is never written to disk), {kind:"remote", url}, or
    {kind:"media", name} for a file already in clip-stacker/media.
    """
    _require_auth(authorization)
    if body.type == "rife" and not settings.clip_stacker_rife_command:
        raise HTTPException(status_code=501, detail="RIFE is not configured on this server (CLIP_STACKER_RIFE_COMMAND)")

    secrets: dict[str, str] = {}
    sources = [p.source for p in body.parts] if body.parts else [body.source]
    for src in sources:
        if src and src.kind == "drive":
            secrets[src.fileId] = src.accessToken
        if src and src.kind == "remote" and not _own_media_name(src.url):
            try:
                await _assert_public_url(src.url)
            except JobFailed as exc:
                raise HTTPException(status_code=422, detail=str(exc))

    request = json.loads(body.model_dump_json(exclude_none=True, exclude={"parts": {"__all__": {"source": {"accessToken"}}}, "source": {"accessToken"}}))
    _prune_old_jobs()
    job_id = uuid.uuid4().hex
    now = _now_iso()
    job = {
        "id": job_id, "type": body.type, "status": "queued", "stage": "queued", "progress": 0.0,
        "createdAt": now, "updatedAt": now, "request": request, "result": None, "error": None,
    }
    _write_job(job)
    _schedule(job_id, secrets)
    return _public_job(job)


@jobs_router.get("/{job_id}", response_model=dict)
async def get_job(job_id: str, authorization: Optional[str] = Header(None)):
    _require_auth(authorization)
    job = _read_job(job_id)
    if job["status"] in ACTIVE_STATUSES and job_id not in _tasks:
        # The worker died with the previous server process; don't leave clients polling forever.
        job = _update_job(job_id, status="failed", stage="failed", error="Job was interrupted by a server restart")
    return _public_job(job)


@jobs_router.post("/{job_id}/cancel", response_model=dict)
async def cancel_job(job_id: str, authorization: Optional[str] = Header(None)):
    _require_auth(authorization)
    job = _read_job(job_id)
    if job["status"] not in ACTIVE_STATUSES:
        raise HTTPException(status_code=409, detail=f"Job already {job['status']}")
    job = _update_job(job_id, status="cancelled", stage="cancelled")
    task = _tasks.get(job_id)
    if task:
        task.cancel()
    return _public_job(job)
