"""Pydantic models shared across the FastAPI application."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class WebhookPayload(BaseModel):
    """Generic webhook payload accepted by every inbound endpoint."""

    source: str = Field(..., description="Name of the sending application, e.g. 'shopify'")
    event: str = Field(..., description="Event type, e.g. 'order.created'")
    timestamp: datetime | None = Field(default=None, description="ISO-8601 event timestamp")
    data: dict[str, Any] = Field(default_factory=dict, description="Arbitrary event payload")


class WebhookResponse(BaseModel):
    status: str
    message: str
    file: str | None = None


class SyncRequest(BaseModel):
    source_url: str = Field(..., description="URL to download and push to FTP")
    destination: str = Field(..., description="Relative path inside FTP_UPLOAD_DIR")
    overwrite: bool = False


class SyncResponse(BaseModel):
    status: str
    destination: str
    bytes_transferred: int = 0
