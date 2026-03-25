#!/usr/bin/env python3
"""
ftp_sync.py – Sync a local directory to FTP.

Usage:
    python scripts/ftp_sync.py [--source /path/to/dir] [--dest /remote/path]

Environment variables: FTP_HOST, FTP_PORT, FTP_USER, FTP_PASS, FTP_TLS,
                       FTP_UPLOAD_DIR, FILES_DIR, LOG_LEVEL
"""

from __future__ import annotations

import argparse
import ftplib
import ssl
import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).parent.parent / "packages"))

from shared.config.config import load_config
from shared.logger.logger import get_logger

config = load_config()
log = get_logger("ftp_sync", level=config.get("LOG_LEVEL", "INFO"))


def get_ftp() -> ftplib.FTP:
    tls = config.get("FTP_TLS", "false").lower() == "true"
    ftp: ftplib.FTP
    if tls:
        ctx = ssl.create_default_context()
        ftp = ftplib.FTP_TLS(context=ctx)
    else:
        ftp = ftplib.FTP()
    ftp.connect(config["FTP_HOST"], int(config.get("FTP_PORT", "21")), timeout=30)
    ftp.login(config["FTP_USER"], config["FTP_PASS"])
    if tls and isinstance(ftp, ftplib.FTP_TLS):
        ftp.prot_p()
    return ftp


def sync_dir(source: Path, remote_base: str) -> None:
    ftp = get_ftp()
    try:
        for local_file in sorted(source.rglob("*")):
            if not local_file.is_file():
                continue
            rel = local_file.relative_to(source)
            remote_path = str(PurePosixPath(remote_base) / str(rel).replace("\\", "/"))

            # Ensure remote directory
            remote_dir = str(PurePosixPath(remote_path).parent)
            parts = PurePosixPath(remote_dir).parts
            for i in range(1, len(parts) + 1):
                d = str(PurePosixPath(*parts[:i]))
                try:
                    ftp.mkd(d)
                except ftplib.error_perm:
                    pass

            with local_file.open("rb") as f:
                ftp.storbinary(f"STOR {remote_path}", f)
            log.info("Uploaded %s → %s", local_file, remote_path)
    finally:
        try:
            ftp.quit()
        except Exception:
            pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync local directory to FTP")
    parser.add_argument("--source", default=config.get("FILES_DIR", "/data/files"), help="Local directory to sync")
    parser.add_argument("--dest", default=config.get("FTP_UPLOAD_DIR", "/home/ftpbridge/files"), help="Remote FTP destination")
    args = parser.parse_args()

    source = Path(args.source)
    if not source.is_dir():
        log.error("Source directory does not exist: %s", source)
        sys.exit(1)

    log.info("Syncing %s → ftp://%s%s", source, config.get("FTP_HOST", ""), args.dest)
    sync_dir(source, args.dest)
    log.info("Sync complete")
