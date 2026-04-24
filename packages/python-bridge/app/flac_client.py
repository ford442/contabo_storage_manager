"""Async client for registering uploaded songs with an external FLAC Player backend."""

import logging
from typing import Optional

import httpx

from .config import settings

logger = logging.getLogger(__name__)



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
) -> Optional[dict]:
    """
    Send uploaded file metadata to the external FLAC Player backend.

    Code
    Now includes tags, genre, duration and storage filename so the FLAC Player
    receives full metadata to index into its library.
    """
    url = settings.flac_player_api_url
    if not url:
        logger.debug("FLAC_PLAYER_API_URL not configured; skipping external registration")
        return None

    clean_title = title or filename.rsplit(".", 1)[0].replace("_", " ").title()

    payload = {
        "name": filename,
        "title": clean_title,
        "author": author,
        "url": public_url,
        "auto_enrich": auto_enrich,
    }
    if tags is not None:
        payload["tags"] = tags
    if genre:
        payload["genre"] = genre
    if duration is not None:
        payload["duration"] = duration
    if filename_on_storage:
        payload["filename"] = filename_on_storage

    # Include optional fields if provided
    if tags:
        payload["tags"] = tags
    if genre:
        payload["genre"] = genre
    if duration is not None:
        payload["duration"] = duration
    if filename_on_storage:
        payload["filename"] = filename_on_storage

    try:
        logger.debug("Registering song with FLAC Player payload=%s", payload)
        async with httpx.AsyncClient(timeout=30.0) as client:
            logger.debug("FLAC registration POST %s payload=%s", url, payload)
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            logger.debug("FLAC Player registration response=%s", data)
            logger.info(
                "Successfully registered %s with FLAC Player (ID: %s)",
                filename,
                data.get("id"),
            )
            logger.debug("FLAC registration response=%s", data)
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

