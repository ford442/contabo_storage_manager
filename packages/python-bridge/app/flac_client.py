"""Async client for registering uploaded songs with an external FLAC Player backend."""

import logging
from typing import Optional

import httpx

from .config import settings

logger = logging.getLogger(__name__)


async def register_song_with_flac_player(
    filename: str,
    public_url: str,
    title: Optional[str] = None,
    author: str = "Noah",
    auto_enrich: bool = True,
) -> Optional[dict]:
    """Send uploaded file metadata to the external FLAC Player backend.

    Args:
        filename: Original filename (including extension).
        public_url: Publicly accessible URL for the audio file.
        title: Song title. If None, derived from the filename.
        author: Artist / author name.
        auto_enrich: Whether the downstream backend should query MusicBrainz
            for extra metadata.

    Returns:
        Parsed JSON response from the backend, or None if the call failed
        or no backend URL is configured.
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

    try:
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
