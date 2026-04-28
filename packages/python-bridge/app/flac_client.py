"""Async + sync clients for registering uploaded songs with an external FLAC Player backend."""

import logging
from typing import Optional

import httpx

from .config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared payload builder
# ---------------------------------------------------------------------------
def _build_payload(
    filename: str,
    public_url: str,
    title: str | None = None,
    author: str = "Noah",
    tags: Optional[list] = None,
    genre: Optional[str] = None,
    duration: Optional[float] = None,
    filename_on_storage: Optional[str] = None,
    auto_enrich: bool = True,
    song_id: Optional[str] = None,
) -> dict:
    """Build the JSON payload expected by flac_player /api/upload/songs."""
    clean_title = title or filename.rsplit(".", 1)[0].replace("_", " ").title()

    payload = {
        "id": song_id,
        "name": filename,
        "title": clean_title,
        "author": author,
        "url": public_url,
        "auto_enrich": auto_enrich,
        "type": "audio",
    }

    if tags is not None:
        payload["tags"] = tags
    if genre:
        payload["genre"] = genre
    if duration is not None:
        payload["duration"] = duration
    if filename_on_storage:
        payload["filename"] = filename_on_storage

    return payload


# ---------------------------------------------------------------------------
# Async client (used by FastAPI upload endpoints)
# ---------------------------------------------------------------------------
async def register_song_with_flac_player(
    filename: str,
    public_url: str,
    title: str | None = None,
    author: str = "Noah",
    tags: Optional[list] = None,
    genre: Optional[str] = None,
    duration: Optional[float] = None,
    filename_on_storage: Optional[str] = None,
    auto_enrich: bool = True,
    song_id: Optional[str] = None,
) -> Optional[dict]:
    """
    Send uploaded file metadata to the external FLAC Player backend (async).

    Returns the decoded JSON response on success, or None on failure / when
    FLAC_PLAYER_API_URL is not configured.
    """
    url = settings.flac_player_api_url
    if not url:
        logger.debug("FLAC_PLAYER_API_URL not configured; skipping external registration")
        return None

    payload = _build_payload(
        filename=filename,
        public_url=public_url,
        title=title,
        author=author,
        tags=tags,
        genre=genre,
        duration=duration,
        filename_on_storage=filename_on_storage,
        auto_enrich=auto_enrich,
        song_id=song_id,
    )

    try:
        logger.info("Registering song with FLAC Player: %s -> %s", filename, url)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            logger.info(
                "Successfully registered %s with FLAC Player (ID: %s)",
                filename,
                data.get("id"),
            )
            return data
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Failed to register %s with FLAC Player: HTTP %s - %s",
            filename,
            exc.response.status_code,
            exc.response.text,
        )
    except httpx.RequestError as exc:
        logger.error(
            "Failed to register %s with FLAC Player: %s",
            filename,
            exc,
        )
    return None


# ---------------------------------------------------------------------------
# Synchronous client (used by file watcher & scripts)
# ---------------------------------------------------------------------------
def register_song_with_flac_player_sync(
    filename: str,
    public_url: str,
    title: str | None = None,
    author: str = "Noah",
    tags: Optional[list] = None,
    genre: Optional[str] = None,
    duration: Optional[float] = None,
    filename_on_storage: Optional[str] = None,
    auto_enrich: bool = True,
    song_id: Optional[str] = None,
) -> Optional[dict]:
    """
    Send uploaded file metadata to the external FLAC Player backend (sync).

    This blocking variant is safe to call from synchronous contexts such as
    watchdog file-system event handlers or CLI scripts.
    """
    url = settings.flac_player_api_url
    if not url:
        logger.debug("FLAC_PLAYER_API_URL not configured; skipping external registration")
        return None

    payload = _build_payload(
        filename=filename,
        public_url=public_url,
        title=title,
        author=author,
        tags=tags,
        genre=genre,
        duration=duration,
        filename_on_storage=filename_on_storage,
        auto_enrich=auto_enrich,
        song_id=song_id,
    )

    try:
        logger.info("[sync] Registering song with FLAC Player: %s -> %s", filename, url)
        with httpx.Client(timeout=30.0) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            logger.info(
                "[sync] Successfully registered %s with FLAC Player (ID: %s)",
                filename,
                data.get("id"),
            )
            return data
    except httpx.HTTPStatusError as exc:
        logger.error(
            "[sync] Failed to register %s with FLAC Player: HTTP %s - %s",
            filename,
            exc.response.status_code,
            exc.response.text,
        )
    except httpx.RequestError as exc:
        logger.error(
            "[sync] Failed to register %s with FLAC Player: %s",
            filename,
            exc,
        )
    return None
