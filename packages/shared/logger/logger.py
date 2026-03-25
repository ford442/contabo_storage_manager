"""Shared structured logger for standalone Python scripts."""

from __future__ import annotations

import logging
import sys
from pathlib import Path


def get_logger(name: str = "ftpbridge", log_file: str | None = None, level: str = "INFO") -> logging.Logger:
    lvl = getattr(logging, level.upper(), logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    logger = logging.getLogger(name)
    logger.setLevel(lvl)

    if not logger.handlers:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        logger.addHandler(sh)

        if log_file:
            try:
                Path(log_file).parent.mkdir(parents=True, exist_ok=True)
                fh = logging.FileHandler(log_file)
                fh.setFormatter(fmt)
                logger.addHandler(fh)
            except OSError:
                pass

    return logger
