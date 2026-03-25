"""Shared FTP utilities (Python)."""

from __future__ import annotations

import ftplib
import io
import ssl
from pathlib import PurePosixPath


def make_ftp_connection(
    host: str,
    port: int,
    user: str,
    password: str,
    tls: bool = False,
) -> ftplib.FTP:
    """Create and return an authenticated FTP(S) connection."""
    if tls:
        ctx = ssl.create_default_context()
        ftp: ftplib.FTP = ftplib.FTP_TLS(context=ctx)
    else:
        ftp = ftplib.FTP()

    ftp.connect(host, port, timeout=15)
    ftp.login(user, password)

    if tls and isinstance(ftp, ftplib.FTP_TLS):
        ftp.prot_p()

    return ftp


def ensure_remote_dirs(ftp: ftplib.FTP, remote_path: PurePosixPath) -> None:
    """Recursively create remote directories if they do not exist."""
    parts = remote_path.parts
    for i in range(1, len(parts) + 1):
        d = str(PurePosixPath(*parts[:i]))
        try:
            ftp.mkd(d)
        except ftplib.error_perm:
            pass  # already exists


def upload_bytes_to_ftp(
    ftp: ftplib.FTP,
    data: bytes,
    remote_path: str,
) -> int:
    """Upload bytes to an open FTP connection. Returns bytes uploaded."""
    remote = PurePosixPath(remote_path)
    ensure_remote_dirs(ftp, remote.parent)
    buf = io.BytesIO(data)
    ftp.storbinary(f"STOR {remote}", buf)
    return len(data)
