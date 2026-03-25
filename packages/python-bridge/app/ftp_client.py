"""FTP helper: uploads files to vsftpd via ftplib (stdlib, no extra deps)."""

from __future__ import annotations

import ftplib
import io
import ssl
from pathlib import Path, PurePosixPath

from .config import get_settings
from .logger import get_logger

log = get_logger("ftp")


def _connect() -> ftplib.FTP:
    settings = get_settings()
    if settings.ftp_tls:
        ctx = ssl.create_default_context()
        ftp: ftplib.FTP = ftplib.FTP_TLS(context=ctx)
    else:
        ftp = ftplib.FTP()

    ftp.connect(settings.ftp_host, settings.ftp_port, timeout=15)
    ftp.login(settings.ftp_user, settings.ftp_pass)

    if settings.ftp_tls and isinstance(ftp, ftplib.FTP_TLS):
        ftp.prot_p()

    return ftp


def _ensure_dirs(ftp: ftplib.FTP, remote_path: PurePosixPath) -> None:
    """Recursively create remote directories if they do not exist."""
    parts = remote_path.parts
    for i in range(1, len(parts) + 1):
        d = str(PurePosixPath(*parts[:i]))
        try:
            ftp.mkd(d)
        except ftplib.error_perm:
            pass  # already exists


def upload_bytes(data: bytes, remote_relative_path: str) -> int:
    """Upload *data* to FTP. Returns bytes uploaded."""
    settings = get_settings()
    base = PurePosixPath(settings.ftp_upload_dir)
    remote = base / remote_relative_path.lstrip("/")

    ftp = _connect()
    try:
        _ensure_dirs(ftp, remote.parent)
        buf = io.BytesIO(data)
        ftp.storbinary(f"STOR {remote}", buf)
        log.info("FTP upload OK → %s (%d bytes)", remote, len(data))
        return len(data)
    finally:
        try:
            ftp.quit()
        except Exception:
            pass


def upload_file(local_path: str | Path, remote_relative_path: str) -> int:
    """Upload a local file to FTP. Returns bytes uploaded."""
    local = Path(local_path)
    data = local.read_bytes()
    return upload_bytes(data, remote_relative_path)
