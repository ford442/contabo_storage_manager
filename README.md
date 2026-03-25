# contabo_storage_manager

> **Lightweight FTP bridge / storage manager for a Contabo VPS.**  
> Receives webhooks from external web apps, persists payloads as timestamped files, and syncs them to vsftpd via FTP – all in a single Docker Compose stack.

Both a **Python (FastAPI)** and a **Node.js (Express)** bridge are provided side-by-side.  
Pick the one you prefer, or run both at the same time.

---

## Table of Contents

- [Architecture](#architecture)
- [Folder Structure](#folder-structure)
- [Quick Start – Contabo Ubuntu VPS](#quick-start--contabo-ubuntu-vps)
- [Running with Docker](#running-with-docker)
- [Running without Docker (systemd)](#running-without-docker-systemd)
- [Environment Variables](#environment-variables)
- [Webhook Endpoints](#webhook-endpoints)
- [Extending the Bridge](#extending-the-bridge)
- [Scripts](#scripts)

---

## Architecture

```
Internet ──→ (nginx / direct)
               │
               ├─── :8000  Python Bridge (FastAPI)
               │             ├── POST /webhook/generic
               │             ├── POST /webhook/shopify
               │             └── POST /webhook/github
               │
               └─── :3000  Node Bridge (Express)
                             ├── POST /webhook/generic
                             ├── POST /webhook/shopify
                             └── POST /webhook/github

Both bridges write to /home/ftpbridge/files  ←── vsftpd already serves this
```

---

## Folder Structure

```
contabo_storage_manager/
├── README.md
├── .env.example
├── docker-compose.yml
├── Dockerfile.python
├── Dockerfile.node
├── pyproject.toml               # Python project metadata & dev deps
├── package.json                 # Node.js root (workspace scripts)
├── packages/
│   ├── shared/                  # Common utilities
│   │   ├── ftp/
│   │   │   ├── ftp_utils.py     # Python FTP helpers
│   │   │   └── ftpUtils.js      # Node.js FTP helpers
│   │   ├── logger/
│   │   │   └── logger.py        # Shared Python logger
│   │   └── config/
│   │       └── config.py        # Shared Python config loader
│   ├── python-bridge/           # FastAPI service
│   │   ├── requirements.txt
│   │   └── app/
│   │       ├── __init__.py
│   │       ├── main.py          # FastAPI app + lifespan
│   │       ├── webhooks.py      # Webhook router
│   │       ├── sync.py          # Background poll loop
│   │       ├── models.py        # Pydantic models
│   │       ├── config.py        # Settings (pydantic-settings)
│   │       ├── logger.py        # Structured logger
│   │       └── ftp_client.py    # FTP upload helpers
│   └── node-bridge/             # Express.js service
│       ├── package.json
│       └── src/
│           ├── index.js         # Express app entry point
│           ├── webhooks.js      # Webhook handlers
│           └── logger.js        # Winston logger
├── scripts/
│   ├── poll_api.py              # Standalone API poller
│   ├── ftp_sync.py              # Sync local dir → FTP
│   └── listFtpFiles.js          # List FTP contents
├── config/
│   ├── vsftpd.conf.example      # vsftpd configuration reference
│   └── nginx.conf.example       # Nginx reverse proxy example
├── systemd/
│   ├── ftpbridge-python.service
│   └── ftpbridge-node.service
└── .gitignore
```

---

## Quick Start – Contabo Ubuntu VPS

> Prerequisites: vsftpd is already installed and serving `/home/ftpbridge/files`.  
> These steps install Docker and launch the bridge in under five minutes.

```bash
# 1. Install Docker + Compose plugin (Ubuntu 22.04 / 24.04)
sudo apt-get update && sudo apt-get install -y ca-certificates curl gnupg
sudo install -m0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt-get update && sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo usermod -aG docker $USER && newgrp docker

# 2. Clone the repo
git clone https://github.com/ford442/contabo_storage_manager.git
cd contabo_storage_manager

# 3. Set up environment
cp .env.example .env
nano .env   # set FTP_PASS, WEBHOOK_SECRET, etc.

# 4. Ensure the FTP files directory exists
sudo mkdir -p /home/ftpbridge/files
sudo chown -R ftpbridge:ftpbridge /home/ftpbridge/files   # or your FTP user

# 5. Start everything
docker compose --profile full up -d

# 6. Verify
curl http://localhost:8000/health   # Python bridge
curl http://localhost:3000/health   # Node bridge
```

---

## Running with Docker

### Start both services

```bash
docker compose --profile full up -d
```

### Start Python bridge only

```bash
docker compose --profile python up -d
```

### Start Node bridge only

```bash
docker compose --profile node up -d
```

### View logs

```bash
docker compose logs -f python-bridge
docker compose logs -f node-bridge
```

### Stop everything

```bash
docker compose --profile full down
```

### Rebuild after code changes

```bash
docker compose --profile full up -d --build
```

---

## Running without Docker (systemd)

### Python bridge

```bash
# 1. Install Python 3.12+ and create virtualenv
sudo apt-get install -y python3.12 python3.12-venv
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r packages/python-bridge/requirements.txt

# 2. Install the systemd service
sudo cp systemd/ftpbridge-python.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ftpbridge-python

# Check status
sudo systemctl status ftpbridge-python
journalctl -u ftpbridge-python -f
```

### Node bridge

```bash
# 1. Install Node.js 20+ (via nvm or NodeSource)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs

# 2. Install dependencies
cd packages/node-bridge && npm ci --omit=dev && cd ../..

# 3. Install the systemd service
sudo cp systemd/ftpbridge-node.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ftpbridge-node

# Check status
sudo systemctl status ftpbridge-node
journalctl -u ftpbridge-node -f
```

---

## Environment Variables

Copy `.env.example` to `.env` and adjust the values.

| Variable | Default | Description |
|---|---|---|
| `APP_ENV` | `production` | `development` or `production` |
| `FTP_HOST` | `127.0.0.1` | vsftpd host |
| `FTP_PORT` | `21` | vsftpd port |
| `FTP_USER` | `ftpbridge` | FTP username |
| `FTP_PASS` | *(empty)* | FTP password – **always set this** |
| `FTP_UPLOAD_DIR` | `/home/ftpbridge/files` | Root path on the FTP server |
| `FTP_TLS` | `false` | Enable FTPS (`true`/`false`) |
| `WEBHOOK_SECRET` | *(empty)* | HMAC secret – leave empty to disable verification |
| `WEBHOOK_HMAC_ALGO` | `sha256` | HMAC algorithm (`sha256` or `sha1`) |
| `PYTHON_PORT` | `8000` | Port for FastAPI service |
| `NODE_PORT` | `3000` | Port for Express service |
| `CORS_ORIGINS` | `*` | Comma-separated CORS origins, or `*` for all |
| `FILES_DIR` | `/home/ftpbridge/files` | Local volume mount path inside container |
| `LOG_LEVEL` | `info` | `debug`, `info`, `warning`, `error` |
| `LOG_FILE` | `/var/log/ftpbridge/app.log` | Log file path |
| `POLL_INTERVAL_SECONDS` | `60` | How often to poll external API |
| `EXTERNAL_API_URL` | *(empty)* | URL to poll for records |
| `EXTERNAL_API_KEY` | *(empty)* | Bearer token for external API |

---

## Webhook Endpoints

Both bridges expose the same routes. Replace `:PORT` with `8000` (Python) or `3000` (Node).

### Health check

```
GET http://VPS_IP:PORT/health
```

### Generic webhook

Accepts any JSON body with `source`, `event`, and `data` fields.

```bash
curl -X POST http://VPS_IP:PORT/webhook/generic \
  -H "Content-Type: application/json" \
  -H "X-Hub-Signature-256: sha256=$(echo -n '{"source":"myapp","event":"user.created","data":{}}' | openssl dgst -sha256 -hmac 'YOUR_SECRET' | awk '{print $2}')" \
  -d '{"source":"myapp","event":"user.created","data":{"id":1,"email":"user@example.com"}}'
```

### Shopify webhook

```bash
curl -X POST http://VPS_IP:PORT/webhook/shopify \
  -H "Content-Type: application/json" \
  -H "X-Shopify-Topic: orders/create" \
  -H "X-Shopify-Hmac-Sha256: <base64-hmac>" \
  -d '{"id":1234,"email":"customer@example.com","total_price":"49.99"}'
```

### GitHub webhook

```bash
curl -X POST http://VPS_IP:PORT/webhook/github \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: push" \
  -H "X-Hub-Signature-256: sha256=<hex-hmac>" \
  -d '{"ref":"refs/heads/main","repository":{"full_name":"org/repo"}}'
```

### Saved file format

Each payload is saved as a timestamped JSON file under `FILES_DIR`:

```
webhooks/
└── shopify/
    └── shopify_orders_create_20240315T143022123456.json
```

---

## Extending the Bridge

### Add a new webhook source (Python)

1. Add a new route in `packages/python-bridge/app/webhooks.py`:

```python
@router.post("/myapp", response_model=WebhookResponse)
async def webhook_myapp(request: Request, x_myapp_signature: str | None = Header(default=None)):
    body = await request.body()
    _verify_signature(body, x_myapp_signature)
    data = json.loads(body)
    payload = WebhookPayload(source="myapp", event=data.get("event", "unknown"), data=data)
    rel_path = _save_payload(payload, body)
    return WebhookResponse(status="ok", message="MyApp payload received", file=rel_path)
```

### Add a new webhook source (Node)

1. Add a handler in `packages/node-bridge/src/webhooks.js`:

```js
async function handleMyApp(req, res) {
  const rawBody = req.rawBody;
  if (!verifySignature(rawBody, req.headers["x-myapp-signature"], res)) return;
  const data = JSON.parse(rawBody);
  const relPath = await savePayload("myapp", data.event || "unknown", rawBody);
  res.json({ status: "ok", file: relPath });
}
module.exports = { ..., handleMyApp };
```

2. Register it in `src/index.js`:

```js
app.post("/webhook/myapp", (req, res) => handleMyApp(req, res).catch(...));
```

---

## Scripts

| Script | Runtime | Description |
|---|---|---|
| `scripts/poll_api.py` | Python | Poll external API and push JSON-lines to FTP |
| `scripts/ftp_sync.py` | Python | Sync entire local directory to FTP |
| `scripts/listFtpFiles.js` | Node.js | List files on FTP server |

```bash
# Poll once
python scripts/poll_api.py --once

# Continuous polling (uses POLL_INTERVAL_SECONDS from .env)
python scripts/poll_api.py

# Sync local dir to FTP
python scripts/ftp_sync.py --source /home/ftpbridge/files --dest /home/ftpbridge/files

# List FTP contents
node scripts/listFtpFiles.js --remote /home/ftpbridge/files
```

---

## License

MIT
