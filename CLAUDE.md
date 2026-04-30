# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Project Overview

**Contabo Storage Manager** is a multi-service webhook bridge and static file server running on a Contabo Ubuntu VPS. It receives webhooks from external web applications, persists payloads and uploaded files locally, and serves them back via REST APIs or static file serving.

The project consists of:
- **Python Bridge (FastAPI)** on port 8000 — primary service handling webhooks, file uploads, and API endpoints
- **Node Bridge (Express)** on port 3000 — lightweight webhook receiver (optional)
- **Nginx static server** on port 8080 — dedicated file serving with range request support
- **vsftpd** — FTP server for remote file access

All services share a single shared directory (`/home/ftpbridge/files` on VPS, `/data/files` in Docker).

---

## Quick Start

### Development Locally

```bash
# Clone and set up environment
cp .env.example .env
# Edit .env with your FTP credentials and settings

# Install Python dev dependencies
pip install -e ".[dev]"

# Run Python bridge locally
uvicorn packages/python-bridge/app/main:app --reload --port 8000

# Run tests
pytest
pytest tests/test_presets_router.py -xvs  # Single test file
pytest -k "test_cors" -xvs               # Tests matching pattern

# Lint and format
ruff check packages/python-bridge/
ruff format packages/python-bridge/
```

### Docker

```bash
# Start everything (Python + Node + Nginx)
docker compose --profile full up -d

# Start just Python bridge
docker compose --profile python up -d

# Rebuild after code changes
docker compose --profile full up -d --build

# View logs
docker compose logs -f python-bridge
```

### Production (systemd)

```bash
# Python bridge
sudo systemctl status ftpbridge-python
sudo systemctl restart ftpbridge-python
journalctl -u ftpbridge-python -f

# Node bridge
sudo systemctl status ftpbridge-node
sudo systemctl restart ftpbridge-node
```

---

## Architecture

### Core Services

**Python Bridge (packages/python-bridge/)**
- Entry point: `app/main.py` — FastAPI application with lifespan management
- Routers: Each domain gets its own router file (`audio_router.py`, `sequencer_router.py`, etc.)
- Webhooks: `app/webhooks.py` — handles incoming payloads from external apps
- Configuration: `app/config.py` (pydantic-settings) — loaded from `.env`
- File I/O: `app/ftp_client.py` — FTP upload helpers; `app/file_watcher.py` — auto-indexing

**Node Bridge (packages/node-bridge/)**
- Entry point: `src/index.js` — Express server for webhook endpoints
- Webhook handlers: `src/webhooks.js`
- Minimal feature set; primarily used when Python bridge is unavailable

**Shared Code (packages/shared/)**
- FTP utilities, logger configuration, etc. (not actively used in current version)

**Scripts (scripts/)**
- `poll_api.py` — background task to poll external APIs and push results
- `ftp_sync.py` — sync local directory to FTP
- Various data sync scripts for music, presets, etc.

### Key Endpoints

| Route | Purpose |
|-------|---------|
| `GET /health` | Health check |
| `GET /admin` | Drag-and-drop upload dashboard (HTML form) |
| `POST /webhook/generic`, `/webhook/github`, `/webhook/shopify` | Generic webhook receivers |
| `POST /webhook/image-effects`, `/webhook/sequencer` | App-specific webhooks |
| `GET /api/songs`, `POST /api/songs/upload` | Audio library management |
| `GET /api/notes/*`, `POST /api/notes/write/*` | Markdown notes storage |
| `GET /api/shaders`, `POST /api/shaders` | Shader metadata and code |
| `GET /files/{path}` | Static file serving |
| `GET /models/*` | WebLLM model serving (with HTTP range header support) |
| `GET /api/presets/*`, `/api/mods/*` | MilkDrop preset and MOD file serving |

---

## File Structure

```
packages/python-bridge/
├── app/
│   ├── __init__.py
│   ├── main.py              ← FastAPI app definition + startup/shutdown hooks
│   ├── config.py            ← Settings (pydantic-settings)
│   ├── models.py            ← Pydantic request/response models
│   ├── cors.py              ← CORS configuration builder
│   ├── logger.py            ← Structured logging setup
│   │
│   ├── webhooks.py          ← Generic, GitHub, Shopify webhook handlers
│   ├── api.py               ← Shader and image API endpoints
│   ├── sync.py              ← Background sync loop
│   ├── file_watcher.py      ← Watchdog file monitoring for auto-indexing
│   ├── ftp_client.py        ← FTP/SFTP upload helpers
│   │
│   ├── *_router.py          ← Domain-specific routers:
│   │   ├── audio_router.py       → /api/songs, /api/music (music library)
│   │   ├── sequencer_router.py   → /api/songs, /api/patterns, /api/samples (sequencer)
│   │   ├── notes_router.py       → /api/notes/* (markdown notes)
│   │   ├── models_router.py      → /models/* (WebLLM models)
│   │   ├── presets_router.py     → /api/presets/* (MilkDrop presets)
│   │   ├── mod_router.py         → /api/mods/* (MOD music files)
│   │   ├── pachinball_router.py  → /maps, /music, /zones (game data)
│   │   ├── leaderboard_router.py → /api/leaderboard* (high scores)
│   │   ├── adventure_router.py   → /api/adventure/* (game progress)
│   │   └── vps_browser_router.py → /api/vps/* (remote file browser)
│   │
│   ├── flac_client.py       ← FLAC metadata extraction
│   ├── templates/           ← Jinja2 HTML templates (admin panel)
│   └── [other routers]
│
├── requirements.txt         ← Python dependencies (mirror of pyproject.toml[dependencies])
├── main_gcs.py             ← Google Cloud Storage sync (standalone script)
│
├── templates/              ← HTML templates for admin dashboard and file browser

tests/
├── test_cors.py
├── test_flac_client.py
├── test_presets_router.py
├── test_api_rating_filters.py
└── conftest.py (if exists)

pyproject.toml              ← Python project metadata, test config, ruff config
docker-compose.yml          ← Service definitions (profiles: python, node, storage, full)
Dockerfile.python           ← Python bridge container image
.env.example                ← Environment variables template
```

---

## Important Patterns

### 1. Router Structure

Each domain (audio, sequencer, notes, etc.) lives in its own `*_router.py`:

```python
from fastapi import APIRouter, File, UploadFile, Header
router = APIRouter(prefix="/api/notes", tags=["notes"])

@router.post("/write/{note_name}")
async def write_note(note_name: str, body: NotePayload):
    # Save to FILES_DIR/notes/{note_name}.md
    # Return JSON response
    pass

@router.get("/read/{note_name}")
async def read_note(note_name: str):
    # Read from FILES_DIR/notes/{note_name}.md
    # Return plaintext or JSON
    pass
```

Routers are included in `main.py` with `app.include_router()`.

### 2. File Paths and Environment

- **Local development**: `FILES_DIR` defaults to `./data/files`
- **Docker**: `FILES_DIR=/data/files` mounted from host
- **VPS production**: `FILES_DIR=/home/ftpbridge/files`

Use `settings.files_dir` (from `config.py`) to access the configured path.

### 3. CORS and Webhook Verification

- CORS middleware is built dynamically from `CORS_ORIGINS` and `CORS_ORIGIN_REGEX` in `.env`
- Webhook signatures use HMAC SHA-256 (or SHA-1) in the `X-Hub-Signature-256` header
- Verification happens in webhook handlers before processing

### 4. File Watcher and Auto-Indexing

`file_watcher.py` monitors `FILES_DIR` for new audio files and auto-indexes them:
- `.flac`, `.mp3`, `.wav`, `.ogg`, `.m4a`, `.aac` files in `audio/music/` are auto-added to `songs.json`
- Metadata extracted using `flac_client.py` (uses `pydub` for multi-format support)
- Triggered on startup and continuously in background

### 5. HTTP Range Requests

`models_router.py` implements HTTP range headers for large file streaming:
- `GET /models/{model_id}` supports `Range: bytes=0-1023` for partial file requests
- Essential for WebLLM clients downloading large ONNX/WASM models
- Check the router for `range_start`, `range_end` logic

---

## Configuration

All settings are in `.env` (loaded by `config.py` using `pydantic-settings`):

| Variable | Default | Notes |
|----------|---------|-------|
| `APP_ENV` | `production` | Set to `development` for verbose logging |
| `FTP_HOST` | `127.0.0.1` | Contabo VPS FTP server IP |
| `FTP_USER` | `ftpbridge` | FTP account name |
| `FTP_PASS` | (required) | FTP password |
| `FILES_DIR` | `/home/ftpbridge/files` | Shared directory (read/write) |
| `WEBHOOK_SECRET` | (empty) | HMAC secret for signature verification |
| `CORS_ORIGINS` | `*` | Comma-separated CORS origins or `*` |
| `CORS_ORIGIN_REGEX` | (built-in) | Regex for trusted origins (`.noahcohn.com`, `.1ink.us`, localhost) |
| `LOG_LEVEL` | `info` | `debug`, `info`, `warning`, `error` |
| `PYTHON_PORT` | `8000` | FastAPI server port |
| `NODE_PORT` | `3000` | Express server port |
| `NGINX_PORT` | `8080` | Nginx static server port |

---

## Testing

### Run All Tests

```bash
pytest
```

### Run Specific Tests

```bash
pytest tests/test_presets_router.py -xvs
pytest -k "test_cors" -xvs
pytest tests/test_flac_client.py::test_extract_metadata -xvs
```

### Testing Webhooks

Tests use `httpx.AsyncClient` for async testing:

```python
from httpx import AsyncClient
from app.main import app

@pytest.mark.asyncio
async def test_webhook():
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/webhook/github", json={...})
        assert response.status_code == 200
```

### Test Configuration

- `pytest.ini_options` in `pyproject.toml` sets `asyncio_mode = "auto"`
- Tests run with `testpaths = ["tests"]`
- Async tests use `pytest-asyncio`

---

## Deployment

### Docker (Recommended)

```bash
docker compose --profile full up -d --build
```

Profiles:
- `python` — Python bridge only
- `node` — Node bridge only
- `storage` — Nginx file server only
- `full` — All three services

Health checks are built into each service definition.

### systemd (Alternative)

The repo includes systemd service files in `systemd/`:
- `ftpbridge-python.service` — Python bridge
- `ftpbridge-node.service` — Node bridge

Install with `sudo cp systemd/ftpbridge-*.service /etc/systemd/system/ && sudo systemctl daemon-reload`.

### CI/CD

GitHub Actions workflow (`.github/workflows/deploy.yml`) auto-deploys on every push to `main`:
1. SSH into VPS
2. `git pull origin main`
3. Run `docker compose --profile full up -d --build` or fallback to systemd
4. Wait for `/health` to respond
5. Optionally trigger music sync

---

## Common Tasks

### Add a New Webhook Endpoint

1. Create a handler in `app/webhooks.py`:

```python
@router.post("/webhook/myapp", response_model=WebhookResponse)
async def webhook_myapp(request: Request, x_myapp_signature: str | None = Header(None)):
    body = await request.body()
    _verify_signature(body, x_myapp_signature, "myapp")  # Verify HMAC
    data = json.loads(body)
    payload = WebhookPayload(source="myapp", event=data.get("event", "unknown"), data=data)
    rel_path = _save_payload(payload, body)  # Save to FILES_DIR
    return WebhookResponse(status="ok", file=rel_path)
```

2. Restart the service: `docker compose up -d --build` or `systemctl restart ftpbridge-python`

### Add a New REST Endpoint

1. Create a new router file, e.g., `app/myfeature_router.py`:

```python
from fastapi import APIRouter
router = APIRouter(prefix="/api/myfeature", tags=["myfeature"])

@router.get("/{item_id}")
async def get_item(item_id: str):
    # Implementation
    return {"status": "ok"}
```

2. Include it in `main.py`:

```python
from .myfeature_router import router as myfeature_router
app.include_router(myfeature_router)
```

### Access Uploaded Files

Routers receive uploaded files via `UploadFile`:

```python
@router.post("/upload")
async def upload_file(file: UploadFile):
    content = await file.read()
    file_path = settings.files_dir / "myfeature" / file.filename
    file_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(file_path, "wb") as f:
        await f.write(content)
    return {"filename": file.filename}
```

### Monitor File Changes

The file watcher in `file_watcher.py` runs at startup. To manually trigger indexing (e.g., for testing):

```python
from app.file_watcher import start_watching
start_watching(settings.files_dir)  # Runs in background thread
```

---

## Dependencies

### Core

- **fastapi** ≥ 0.111.0 — Web framework
- **uvicorn[standard]** ≥ 0.29.0 — ASGI server
- **pydantic** / **pydantic-settings** ≥ 2.7.0 / ≥ 2.2.0 — Data validation and settings
- **aiofiles** ≥ 23.2.1 — Async file I/O
- **httpx** ≥ 0.27.0 — Async HTTP client
- **python-multipart** — Multipart form parsing

### Optional (Already Installed)

- **paramiko** ≥ 3.5.0 — SFTP
- **asyncssh** — SSH command execution (admin panel)
- **google-cloud-storage** — GCS syncing
- **watchdog** — File system monitoring
- **pydub** ≥ 0.25.1 — Audio metadata
- **aiocache** ≥ 0.12.0 — Caching layer
- **gunicorn** ≥ 21.2.0 — Production WSGI alternative

### Dev

- **pytest** ≥ 8.2.0
- **pytest-asyncio** ≥ 0.23.0
- **httpx** ≥ 0.27.0 (also used for client testing)
- **ruff** ≥ 0.4.0 — Linting and formatting

---

## Linting and Formatting

Uses **Ruff** (configured in `pyproject.toml`):

```bash
# Check for issues
ruff check packages/python-bridge/

# Automatically fix
ruff check --fix packages/python-bridge/

# Format code
ruff format packages/python-bridge/
```

Config:
- **Line length**: 120
- **Target**: Python 3.12
- **Rules**: E, F, I, UP (errors, undefined names, imports, upgrades)
- **Ignore**: E501 (long lines — covered by formatter)

---

## Troubleshooting

### App won't start

- Check `.env` is set up (especially `FTP_PASS`)
- Check port 8000 isn't already in use
- Run with verbose logging: `APP_ENV=development uvicorn ... --log-level debug`

### Tests fail with "asyncio.get_event_loop()"

- Ensure `pytest-asyncio` is installed
- `pyproject.toml` has `asyncio_mode = "auto"` — this handles event loop setup

### CORS errors in browser

- Check `CORS_ORIGINS` or `CORS_ORIGIN_REGEX` in `.env`
- CORS middleware is built in `cors.py` — review the regex logic
- For development, set `CORS_ORIGINS=*`

### Webhook signatures don't verify

- Ensure `WEBHOOK_SECRET` is set in both `.env` and the calling app
- Check the header name matches (default: `X-Hub-Signature-256`)
- Verify the HMAC algorithm matches (default: `sha256`)

---

## References

- README.md — Full documentation on endpoints, deployment, and app integrations
- AGENTS.md — Additional context for AI agents (architecture, file structure)
- pyproject.toml — Python project config, dependencies, test settings
- docker-compose.yml — Service definitions and environment
- .env.example — All configurable settings with defaults
