"""Shared config loader for standalone Python scripts."""

from __future__ import annotations

import os
from pathlib import Path


def _load_dotenv(env_file: str = ".env") -> None:
    env_path = Path(env_file)
    if not env_path.exists():
        return
    with env_path.open() as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def load_config(env_file: str = ".env") -> dict[str, str]:
    """Load .env and return a snapshot of relevant env vars."""
    _load_dotenv(env_file)
    keys = [
        "APP_ENV",
        "FTP_HOST", "FTP_PORT", "FTP_USER", "FTP_PASS",
        "FTP_UPLOAD_DIR", "FTP_TLS",
        "WEBHOOK_SECRET", "WEBHOOK_HMAC_ALGO",
        "FILES_DIR", "LOG_LEVEL", "LOG_FILE",
        "POLL_INTERVAL_SECONDS", "EXTERNAL_API_URL", "EXTERNAL_API_KEY",
    ]
    return {k: os.environ.get(k, "") for k in keys}
