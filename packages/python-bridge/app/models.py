"""Pydantic models shared across the FastAPI application."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field
from typing import List, Optional

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


class FileUploadResponse(BaseModel):
    status: str
    message: str
    files: List[str]          # list of relative paths (local)
    remote_files: Optional[List[str]] = None   # optional remote paths from SFTP

class HealthResponse(BaseModel):
    status: str
    service: str


class StorageResult(BaseModel):
    """Response wrapper for storage mutations (matches frontend)."""
    success: bool = True
    id: Optional[str] = None
    url: Optional[str] = None
    timestamp: Optional[str] = None
    action: Optional[str] = None
    error: Optional[str] = None
