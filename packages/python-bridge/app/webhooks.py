"""Webhook router: receives JSON payloads and persists them as files."""

from __future__ import annotations

import hmac
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request, status

from .config import get_settings
from .ftp_client import upload_bytes
from .logger import get_logger
from .models import WebhookPayload, WebhookResponse

router = APIRouter(prefix="/webhook", tags=["webhooks"])
log = get_logger("webhooks")
settings = get_settings()


# ── Signature verification helpers ───────────────────────────────────────────

def _verify_signature(body: bytes, signature_header: str | None) -> None:
    """Raise HTTP 401 if WEBHOOK_SECRET is set and signature does not match."""
    secret = settings.webhook_secret
    if not secret:
        return  # verification disabled

    if not signature_header:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing signature header")

    algo = settings.webhook_hmac_algo
    expected_sig = hmac.new(secret.encode(), body, algo).hexdigest()

    # Support 'sha256=<hex>' style (GitHub/Shopify) or bare hex
    provided = signature_header.split("=", 1)[-1]

    if not hmac.compare_digest(expected_sig, provided):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook signature")


# ── Path-safe name helper ─────────────────────────────────────────────────────

_SAFE_NAME_RE = re.compile(r"[^\w\-]")


def _safe_name(value: str, max_len: int = 64) -> str:
    """Return a filesystem-safe name (alphanumeric + hyphens/underscores only)."""
    return _SAFE_NAME_RE.sub("_", value)[:max_len]


# ── Persistence helper ────────────────────────────────────────────────────────

def _save_payload(payload: WebhookPayload, raw: bytes) -> str:
    """Write payload to local volume and (optionally) FTP. Returns relative path."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    safe_source = _safe_name(payload.source)
    safe_event = _safe_name(payload.event.replace(".", "_"))
    filename = f"{safe_source}_{safe_event}_{ts}.json"
    rel_path = f"webhooks/{safe_source}/{filename}"

    files_dir = Path(settings.files_dir)
    local_path = files_dir / rel_path
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(raw)
    log.info("Saved webhook payload → %s", local_path)

    try:
        upload_bytes(raw, rel_path)
    except Exception as exc:
        log.warning("FTP upload failed (payload still saved locally): %s", exc)

    return rel_path


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.post(
    "/generic",
    response_model=WebhookResponse,
    summary="Generic JSON webhook receiver",
)
async def webhook_generic(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
):
    body = await request.body()
    _verify_signature(body, x_hub_signature_256)

    try:
        data = json.loads(body)
        payload = WebhookPayload(**data)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    rel_path = _save_payload(payload, body)
    return WebhookResponse(status="ok", message="Payload received", file=rel_path)


@router.post(
    "/shopify",
    response_model=WebhookResponse,
    summary="Shopify webhook receiver",
)
async def webhook_shopify(
    request: Request,
    x_shopify_hmac_sha256: str | None = Header(default=None),
):
    body = await request.body()
    _verify_signature(body, x_shopify_hmac_sha256)

    try:
        raw_data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    topic = request.headers.get("x-shopify-topic", "unknown.event")
    payload = WebhookPayload(source="shopify", event=topic, data=raw_data)
    rel_path = _save_payload(payload, body)
    return WebhookResponse(status="ok", message="Shopify payload received", file=rel_path)


@router.post(
    "/github",
    response_model=WebhookResponse,
    summary="GitHub webhook receiver",
)
async def webhook_github(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
):
    body = await request.body()
    _verify_signature(body, x_hub_signature_256)

    try:
        raw_data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    event = x_github_event or "unknown"
    payload = WebhookPayload(source="github", event=event, data=raw_data)
    rel_path = _save_payload(payload, body)
    return WebhookResponse(status="ok", message="GitHub payload received", file=rel_path)
