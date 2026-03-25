"""Structured logger for the Python bridge."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from .config import get_settings


def _build_logger(name: str) -> logging.Logger:
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    logger = logging.getLogger(name)
    logger.setLevel(level)

    if not logger.handlers:
        # Always log to stdout (Docker-friendly)
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        logger.addHandler(sh)

        # Optionally log to file
        log_path = Path(settings.log_file)
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(log_path)
            fh.setFormatter(fmt)
            logger.addHandler(fh)
        except OSError:
            # Non-fatal: file logging may not be available in all environments
            pass

    return logger


def get_logger(name: str = "ftpbridge") -> logging.Logger:
    return _build_logger(name)
