import ftplib
import io
import ssl
from pathlib import Path
from typing import Optional
from .config import settings
from .logger import get_logger

logger = get_logger("ftp_client")


class StorageFTPClient:
    def __init__(
        self,
        host: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        port: Optional[int] = None,
        base_dir: Optional[str] = None,
    ):
        self.host = host or getattr(settings, 'ftp_host', None) or getattr(settings, 'external_ftp_host', None)
        self.user = user or getattr(settings, 'ftp_user', None) or getattr(settings, 'external_ftp_user', None)
        self.password = password or getattr(settings, 'ftp_pass', None) or getattr(settings, 'external_ftp_pass', None)
        self.port = port or getattr(settings, 'ftp_port', None) or getattr(settings, 'external_ftp_port', 21)
        self.base_dir = base_dir or getattr(settings, 'ftp_upload_dir', None) or getattr(settings, 'external_ftp_dir', '/')
        self._conn = None

        logger.info(f"FTP Client initialized: host={self.host}, port={self.port}, user={self.user}, base_dir={self.base_dir}")

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _get_connection(self):
        """Open a fresh FTP or SFTP connection."""
        if self.port == 22:
            return self._get_sftp_connection()
        return self._get_ftps_connection()

    def _get_ftps_connection(self):
        logger.info(f"Connecting to FTPS server {self.host}:{self.port}")
        ftp = ftplib.FTP_TLS()
        ftp.connect(self.host, self.port, timeout=30)
        ftp.login(self.user, self.password)
        ftp.prot_p()
        return ftp

    def _get_sftp_connection(self):
        try:
            import paramiko
        except ImportError:
            logger.error("paramiko is required for SFTP connections (pip install paramiko)")
            raise

        logger.info(f"Connecting to SFTP server {self.host}:{self.port}")
        transport = paramiko.Transport((self.host, self.port))
        transport.connect(username=self.user, password=self.password)
        sftp = paramiko.SFTPClient.from_transport(transport)
        return sftp

    def _is_alive(self) -> bool:
        """Check whether the current connection is still usable."""
        if self._conn is None:
            return False
        try:
            if hasattr(self._conn, 'voidcmd'):  # FTPS
                self._conn.voidcmd("NOOP")
                return True
            else:  # SFTP — check the underlying transport
                transport = self._conn.get_channel().get_transport()
                return transport is not None and transport.is_active()
        except Exception:
            return False

    def _ensure_connected(self):
        """Return a live connection, (re)connecting as needed."""
        if not self._is_alive():
            self._close_conn()
            self._conn = self._get_connection()
        return self._conn

    def _close_conn(self):
        if self._conn is None:
            return
        try:
            if hasattr(self._conn, 'quit'):
                self._conn.quit()
            else:
                self._conn.close()
        except Exception:
            pass
        self._conn = None

    def close(self):
        """Explicitly close and discard the persistent connection."""
        self._close_conn()

    # ------------------------------------------------------------------
    # Directory helpers
    # ------------------------------------------------------------------

    def _ensure_remote_dir(self, conn, rel_path: str):
        """Recursively create directories on the remote server."""
        parts = rel_path.strip("/").split("/")
        current_path = self.base_dir.rstrip("/")

        for part in parts:
            current_path = f"{current_path}/{part}"
            try:
                if hasattr(conn, 'cwd'):  # FTPS
                    conn.cwd(current_path)
                else:  # SFTP
                    conn.stat(current_path)
            except (ftplib.error_perm, IOError, OSError):
                logger.info(f"Creating remote directory: {current_path}")
                if hasattr(conn, 'mkd'):
                    conn.mkd(current_path)
                else:
                    conn.mkdir(current_path)

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def upload_bytes(self, data: bytes, remote_rel_path: str) -> bool:
        """Upload raw bytes to the remote server. Raises on failure."""
        if not self.host:
            raise RuntimeError("FTP upload skipped: FTP_HOST not configured")
        if not self.user:
            raise RuntimeError("FTP upload skipped: FTP_USER not configured")
        if not self.password:
            raise RuntimeError("FTP upload skipped: FTP_PASS not configured")

        for attempt in range(2):
            try:
                conn = self._ensure_connected()

                remote_path_obj = Path(remote_rel_path)
                if remote_path_obj.parent != Path("."):
                    self._ensure_remote_dir(conn, str(remote_path_obj.parent))

                target_file = f"{self.base_dir.rstrip('/')}/{remote_rel_path.lstrip('/')}"

                if hasattr(conn, 'storbinary'):  # FTPS
                    conn.storbinary(f"STOR {target_file}", io.BytesIO(data))
                else:  # SFTP
                    conn.putfo(io.BytesIO(data), target_file)

                logger.info(f"Uploaded: {target_file} ({len(data)} bytes)")
                return True

            except (ftplib.error_temp, EOFError, ConnectionResetError, BrokenPipeError, OSError) as e:
                # Likely a stale connection — invalidate and retry once
                logger.warning(f"Connection error on attempt {attempt + 1}, reconnecting: {e}")
                self._close_conn()
                if attempt == 1:
                    logger.error(f"FTP upload failed after retry: {e}")
                    raise

            except Exception as e:
                logger.error(f"FTP upload failed: {e}")
                raise

        return False  # unreachable but satisfies type checker

    # ------------------------------------------------------------------
    # Sync (download from remote)
    # ------------------------------------------------------------------

    def sync_dir_from_remote(self, remote_rel_dir: str, local_dir: Path, extensions: tuple[str, ...] = (), remove_stale: bool = False) -> dict:
        result = {"downloaded": 0, "skipped": 0, "removed": 0, "errors": 0, "total": 0}

        if not self.host or not self.user or not self.password:
            logger.warning("FTP sync skipped: credentials not configured")
            return result

        remote_dir = f"{self.base_dir.rstrip('/')}/{remote_rel_dir.lstrip('/')}"
        local_dir = Path(local_dir)
        local_dir.mkdir(parents=True, exist_ok=True)

        try:
            conn = self._ensure_connected()
            is_sftp = hasattr(conn, 'listdir')

            if is_sftp:
                remote_entries = conn.listdir(remote_dir)
            else:
                conn.cwd(remote_dir)
                remote_entries = conn.nlst()

            remote_files = []
            for entry in remote_entries:
                if extensions:
                    if not any(entry.lower().endswith(ext.lower()) for ext in extensions):
                        continue
                remote_files.append(entry)

            result["total"] = len(remote_files)
            remote_set = set(remote_files)

            for filename in remote_files:
                remote_path = f"{remote_dir}/{filename}"
                local_path = local_dir / filename

                try:
                    if is_sftp:
                        remote_stat = conn.stat(remote_path)
                        remote_size = remote_stat.st_size
                        remote_mtime = remote_stat.st_mtime
                    else:
                        remote_size = conn.size(filename)
                        remote_mtime = None
                except Exception:
                    remote_size = None
                    remote_mtime = None

                if local_path.exists():
                    local_size = local_path.stat().st_size
                    local_mtime = local_path.stat().st_mtime
                    if remote_size is not None and local_size == remote_size:
                        if remote_mtime is None or local_mtime >= remote_mtime:
                            result["skipped"] += 1
                            continue

                logger.info("Downloading %s from %s", filename, remote_rel_dir)
                try:
                    if is_sftp:
                        conn.get(remote_path, str(local_path))
                    else:
                        with local_path.open("wb") as f:
                            conn.retrbinary(f"RETR {filename}", f.write)
                    result["downloaded"] += 1
                except Exception as exc:
                    logger.error("Failed to download %s: %s", filename, exc)
                    result["errors"] += 1

            if remove_stale:
                for local_file in local_dir.iterdir():
                    if local_file.is_file() and local_file.name not in remote_set:
                        if not extensions or any(local_file.name.lower().endswith(ext.lower()) for ext in extensions):
                            local_file.unlink()
                            result["removed"] += 1
                            logger.info("Removed stale local file %s", local_file.name)

        except Exception as exc:
            logger.error("FTP sync failed for %s: %s", remote_rel_dir, exc)
        finally:
            # Leave the persistent connection open; caller or GC will close it.
            pass

        logger.info("Sync %s: %d downloaded, %d skipped, %d removed, %d errors, %d total",
                    remote_rel_dir, result["downloaded"], result["skipped"], result["removed"], result["errors"], result["total"])
        return result

    def sync_mods_from_remote(self, local_dir: Path) -> dict:
        return self.sync_dir_from_remote(
            "mods",
            local_dir,
            extensions=('.mod', '.xm', '.s3m', '.it', '.mptm', '.stm', '.669', '.amf', '.ams', '.dbm', '.dmf', '.dsm', '.far', '.gdm', '.j2b', '.mdl', '.med', '.mtm', '.okt', '.psm', '.ptm', '.ult', '.umx', '.mt2', '.mo3'),
            remove_stale=True
        )


# ------------------------------------------------------------------
# Module-level helpers used by webhooks.py
# ------------------------------------------------------------------

def upload_bytes(data: bytes, rel_path: str):
    client = StorageFTPClient()
    return client.upload_bytes(data, rel_path)


class FTPClientWrapper:
    """Async wrapper for StorageFTPClient."""

    async def upload(self, local_path: Path, rel_path: str) -> Optional[str]:
        import asyncio

        def _upload():
            client = StorageFTPClient()
            data = Path(local_path).read_bytes()
            client.upload_bytes(data, rel_path)
            return rel_path

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _upload)


ftp_client = FTPClientWrapper()
