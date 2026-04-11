# AGENTS.md – Contabo Storage Manager

> This file contains essential context for AI coding agents working on this project.  
> The project is a lightweight FTP bridge / storage manager for a Contabo VPS.

---

## Project Overview

This project provides webhook receivers that persist payloads as timestamped files and sync them to FTP/SFTP. It includes two parallel implementations:

- **Python Bridge** (`packages/python-bridge/`): FastAPI application on port 8000
- **Node Bridge** (`packages/node-bridge/`): Express.js application on port 3000

Both bridges receive webhooks from external applications, save them locally, and optionally upload to an external FTP/SFTP server.

### Supported Webhook Endpoints

| Endpoint | Description |
|----------|-------------|
| `POST /webhook/generic` | Generic JSON webhooks |
| `POST /webhook/github` | GitHub webhook events |
| `POST /webhook/shopify` | Shopify webhook events |
| `POST /webhook/image-effects` | image_video_effects app |
| `POST /webhook/flac` | flac_player app (multipart/form-data) |
| `POST /webhook/sequencer` | web_sequencer app (multipart/form-data) |
| `GET /files/{path}` | Static file server (Python bridge only) |
| `GET /health` | Health check endpoint |

---

## Technology Stack

### Python Bridge
- **Runtime**: Python 3.12+
- **Framework**: FastAPI 0.111+
- **Server**: Uvicorn with standard workers
- **Key Dependencies**: 
  - `pydantic` / `pydantic-settings` for configuration
  - `aiofiles` for async file operations
  - `httpx` for HTTP client
  - `python-multipart` for file uploads
  - `paramiko` for SFTP connections

### Node Bridge
- **Runtime**: Node.js 18+
- **Framework**: Express.js 4.19+
- **Key Dependencies**:
  - `basic-ftp` for FTP operations
  - `winston` for logging
  - `express-rate-limit` for rate limiting
  - `dotenv` for environment configuration

### Infrastructure
- **Containerization**: Docker + Docker Compose
- **Static File Server**: Nginx (port 8080)
- **Deployment Target**: Contabo Ubuntu VPS with vsftpd

---

## Project Structure

```
contabo_storage_manager/
├── packages/
│   ├── python-bridge/          # FastAPI service (port 8000)
│   │   ├── app/
│   │   │   ├── main.py         # FastAPI app entry point
│   │   │   ├── webhooks.py     # Webhook route handlers
│   │   │   ├── models.py       # Pydantic models
│   │   │   ├── config.py       # Settings (pydantic-settings)
│   │   │   ├── ftp_client.py   # FTP/SFTP upload client
│   │   │   ├── logger.py       # Structured logger
│   │   │   └── sync.py         # Background API poll loop
│   │   └── requirements.txt
│   ├── node-bridge/            # Express service (port 3000)
│   │   ├── src/
│   │   │   ├── index.js        # Express app entry point
│   │   │   ├── webhooks.js     # Webhook handlers
│   │   │   └── logger.js       # Winston logger
│   │   └── package.json
│   └── shared/                 # Common utilities
│       ├── ftp/
│       │   ├── ftp_utils.py    # Python FTP helpers
│       │   └── ftpUtils.js     # Node.js FTP helpers
│       ├── logger/
│       │   └── logger.py       # Shared Python logger
│       └── config/
│           └── config.py       # Standalone script config loader
├── scripts/                    # Standalone utilities
│   ├── poll_api.py             # API polling script
│   ├── ftp_sync.py             # Directory sync to FTP
│   └── listFtpFiles.js         # List FTP contents
├── config/
│   ├── nginx-files.conf        # Nginx static server config
│   ├── nginx.conf.example      # Example reverse proxy config
│   └── vsftpd.conf.example     # Example vsftpd config
├── systemd/                    # Systemd service files
│   ├── ftpbridge-python.service
│   └── ftpbridge-node.service
├── docker-compose.yml          # Docker Compose orchestration
├── Dockerfile.python           # Python bridge container
├── Dockerfile.node             # Node bridge container
├── pyproject.toml              # Python project metadata & dev deps
└── package.json                # Node.js workspace scripts
```

---

## Build and Run Commands

### Docker (Recommended)

```bash
# Start both bridges + nginx
docker compose --profile full up -d

# Start Python bridge only
docker compose --profile python up -d

# Start Node bridge only
docker compose --profile node up -d

# Start nginx static server only
docker compose --profile storage up -d

# View logs
docker compose logs -f python-bridge
docker compose logs -f node-bridge

# Rebuild after code changes
docker compose --profile full up -d --build

# Stop everything
docker compose --profile full down
```

### Without Docker (systemd)

**Python bridge:**
```bash
# Setup virtual environment
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r packages/python-bridge/requirements.txt

# Install and start service
sudo cp systemd/ftpbridge-python.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ftpbridge-python
```

**Node bridge:**
```bash
# Install dependencies
cd packages/node-bridge && npm ci --omit=dev

# Install and start service
sudo cp systemd/ftpbridge-node.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ftpbridge-node
```

### Development

```bash
# Node.js development with auto-reload
npm run dev:node

# Or directly
cd packages/node-bridge && npm run dev
```

---

## Environment Configuration

Copy `.env.example` to `.env` and configure:

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_ENV` | `production` | `development` or `production` |
| `FTP_HOST` | `127.0.0.1` | FTP/SFTP server host |
| `FTP_PORT` | `21` | FTP port (22 for SFTP) |
| `FTP_USER` | `ftpbridge` | FTP username |
| `FTP_PASS` | *(empty)* | FTP password (required) |
| `FTP_UPLOAD_DIR` | `/home/ftpbridge/files` | Remote upload directory |
| `FTP_TLS` | `false` | Enable FTPS |
| `WEBHOOK_SECRET` | *(empty)* | HMAC secret for signature verification |
| `WEBHOOK_HMAC_ALGO` | `sha256` | HMAC algorithm (`sha256` or `sha1`) |
| `PYTHON_PORT` | `8000` | Python bridge port |
| `NODE_PORT` | `3000` | Node bridge port |
| `FILES_DIR` | `/home/ftpbridge/files` | Local storage directory |
| `LOG_LEVEL` | `info` | Logging level |
| `LOG_FILE` | `/var/log/ftpbridge/app.log` | Log file path |
| `EXTERNAL_API_URL` | *(empty)* | URL for polling sync |
| `EXTERNAL_API_KEY` | *(empty)* | Bearer token for external API |
| `POLL_INTERVAL_SECONDS` | `60` | Polling interval |

---

## Code Style Guidelines

### Python
- **Formatter**: Ruff (configured in `pyproject.toml`)
- **Line length**: 120 characters
- **Target version**: Python 3.12
- **Lint rules**: E, F, I, UP (ignore E501)

```bash
# Run linter
ruff check packages/python-bridge

# Run formatter
ruff format packages/python-bridge
```

### JavaScript/Node.js
- Use `"use strict";` at the top of files
- Prefer `const` and `let` over `var`
- Use async/await for asynchronous operations
- JSDoc comments for function documentation

---

## Testing Instructions

### Python Tests

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run with coverage
pytest --cov=packages/python-bridge
```

Test configuration in `pyproject.toml`:
- `asyncio_mode = "auto"`
- Test paths: `tests/`

### Manual Testing

```bash
# Health check
curl http://localhost:8000/health
curl http://localhost:3000/health

# Generic webhook
curl -X POST http://localhost:8000/webhook/generic \
  -H "Content-Type: application/json" \
  -H "X-Hub-Signature-256: sha256=<hmac>" \
  -d '{"source":"test","event":"ping","data":{}}'

# File upload (Python bridge)
curl -X POST http://localhost:8000/webhook/flac \
  -H "X-Hub-Signature-256: sha256=<hmac>" \
  -F "action=upload_audio" \
  -F "file=@audio.flac"
```

---

## Security Considerations

### Webhook Signature Verification
- All webhook endpoints support HMAC signature verification
- Signatures expected in `X-Hub-Signature-256` header (or `X-Shopify-Hmac-Sha256` for Shopify)
- Format: `sha256=<hex_digest>`
- Verification is **disabled** if `WEBHOOK_SECRET` is not set
- Uses `hmac.compare_digest()` / `crypto.timingSafeEqual()` to prevent timing attacks

### File Upload Security
- Filename sanitization: only alphanumeric, `._-` allowed
- Path traversal prevention in static file serving
- Max upload size configurable via `MAX_UPLOAD_MB` (default 8GB)

### Network Security
- FTP/SFTP connections use TLS when `FTP_TLS=true`
- Port 22 automatically uses SFTP (paramiko) instead of FTPS
- Rate limiting on webhook endpoints (100 req/min per IP in Node bridge)

### Secrets Management
- Never commit `.env` file (listed in `.gitignore`)
- Use `.env.example` as template without real credentials
- For production, consider using Docker secrets or a secrets manager

---

## Storage Layout

Files are organized under `FILES_DIR` (default `/home/ftpbridge/files`):

```
/home/ftpbridge/files/
├── webhooks/                   # Generic webhook payloads
│   ├── generic/
│   ├── github/
│   └── shopify/
├── image-effects/
│   ├── shaders/               # Shader JSON configs
│   ├── metadata/              # Effect metadata
│   └── outputs/               # Generated outputs (dated)
├── audio/
│   ├── flac/                  # FLAC audio files
│   ├── wav/                   # WAV/AIFF files
│   ├── covers/                # Cover art
│   ├── playlists/             # Playlist JSON
│   └── metadata/              # Track metadata
└── sequencer/
    ├── projects/              # Project JSON files
    ├── midi/                  # MIDI files
    ├── samples/               # Audio samples
    └── recordings/            # Exported recordings
```

---

## Deployment Process

1. **Prerequisites**: vsftpd installed and serving `/home/ftpbridge/files`
2. **Environment**: Copy and configure `.env`
3. **Docker**: Use `docker compose --profile full up -d`
4. **SSL/TLS**: Put a reverse proxy (Caddy/Nginx) in front for HTTPS
5. **Monitoring**: Health endpoints at `/health`, logs in Docker or journald

---

## Key Implementation Details

### Python Bridge
- Uses `pydantic-settings` for environment-based configuration
- FTP client supports both FTPS (port 21) and SFTP (port 22) via paramiko
- Static files served with correct MIME types for audio/video
- Background sync task polls external API if configured

### Node Bridge
- Raw body capture middleware for HMAC verification
- Winston logger with both console and file outputs
- basic-ftp library for FTP operations

### Both Bridges
- Files saved with timestamp prefix: `YYYYMMDDTHHMMSSffffff_<name>`
- External FTP upload is optional; files always saved locally first
- Graceful handling of missing FTP credentials
