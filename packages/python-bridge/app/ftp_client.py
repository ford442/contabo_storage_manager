import ftplib
import ssl
from pathlib import Path
from typing import Optional
from .config import settings
from .logger import get_logger

logger = get_logger("ftp_client")


class StorageFTPClient:
    def __init__(self):
        # Support both EXTERNAL_FTP_* and FTP_* variables
        # Priority: FTP_* (user settings) > EXTERNAL_FTP_* (legacy)
        self.host = getattr(settings, 'ftp_host', None) or getattr(settings, 'external_ftp_host', None)
        self.user = getattr(settings, 'ftp_user', None) or getattr(settings, 'external_ftp_user', None)
        self.password = getattr(settings, 'ftp_pass', None) or getattr(settings, 'external_ftp_pass', None)
        self.port = getattr(settings, 'ftp_port', None) or getattr(settings, 'external_ftp_port', 21)
        self.base_dir = getattr(settings, 'ftp_upload_dir', None) or getattr(settings, 'external_ftp_dir', '/')
        
        # Debug logging
        logger.info(f"FTP Client initialized: host={self.host}, port={self.port}, user={self.user}, base_dir={self.base_dir}")
        logger.info(f"Raw settings: ftp_host={getattr(settings, 'ftp_host', None)}, external_ftp_host={getattr(settings, 'external_ftp_host', None)}")

    def _get_connection(self):
        """Create FTP or SFTP connection based on port."""
        # Port 22 typically indicates SFTP (SSH)
        if self.port == 22:
            return self._get_sftp_connection()
        else:
            return self._get_ftps_connection()

    def _get_ftps_connection(self):
        """Create a secure FTP_TLS connection."""
        logger.info(f"Connecting to FTPS server {self.host}:{self.port}")
        ftp = ftplib.FTP_TLS()
        ftp.connect(self.host, self.port, timeout=30)
        ftp.login(self.user, self.password)
        ftp.prot_p()  # Switch to secure data connection
        return ftp

    def _get_sftp_connection(self):
        """Create an SFTP connection using paramiko."""
        try:
            import paramiko
        except ImportError:
            logger.error("paramiko is required for SFTP connections")
            raise

        logger.info(f"Connecting to SFTP server {self.host}:{self.port}")
        transport = paramiko.Transport((self.host, self.port))
        transport.connect(username=self.user, password=self.password)
        sftp = paramiko.SFTPClient.from_transport(transport)
        return sftp

    def _ensure_remote_dir(self, ftp, rel_path: str):
        """Recursively create directories on the FTP/SFTP server."""
        parts = rel_path.strip("/").split("/")
        current_path = self.base_dir.rstrip("/")
        
        for part in parts:
            current_path = f"{current_path}/{part}"
            try:
                if hasattr(ftp, 'cwd'):  # FTPS
                    ftp.cwd(current_path)
                else:  # SFTP
                    ftp.stat(current_path)
            except (ftplib.error_perm, IOError):
                logger.info(f"Creating remote directory: {current_path}")
                if hasattr(ftp, 'mkd'):  # FTPS
                    ftp.mkd(current_path)
                else:  # SFTP
                    ftp.mkdir(current_path)

    def upload_bytes(self, data: bytes, remote_rel_path: str) -> bool:
        """Upload raw bytes to the remote FTP/SFTP server."""
        if not self.host:
            logger.warning("FTP upload skipped: FTP_HOST not configured")
            return False
        if not self.user:
            logger.warning("FTP upload skipped: FTP_USER not configured")
            return False
        if not self.password:
            logger.warning("FTP upload skipped: FTP_PASS not configured")
            return False

        conn = None
        try:
            conn = self._get_connection()
            
            # 1. Ensure the subfolders exist (e.g., image-effects/shaders)
            remote_path_obj = Path(remote_rel_path)
            if remote_path_obj.parent != Path("."):
                self._ensure_remote_dir(conn, str(remote_path_obj.parent))

            # 2. Upload the file
            target_file = f"{self.base_dir.rstrip('/')}/{remote_rel_path.lstrip('/')}"
            
            if hasattr(conn, 'storbinary'):  # FTPS
                import io
                bio = io.BytesIO(data)
                conn.storbinary(f"STOR {target_file}", bio)
            else:  # SFTP
                import io
                bio = io.BytesIO(data)
                conn.putfo(bio, target_file)
            
            logger.info(f"Successfully uploaded to: {target_file}")
            return True

        except Exception as e:
            logger.error(f"FTP upload failed: {e}")
            return False
        finally:
            if conn:
                try:
                    if hasattr(conn, 'quit'):  # FTPS
                        conn.quit()
                    else:  # SFTP
                        conn.close()
                except:
                    pass

    def sync_mods_from_remote(self, local_dir: Path) -> dict:
        """Download MOD files from remote FTP/SFTP to local directory.

        Returns:
            dict with keys: downloaded, skipped, removed, errors, total
        """
        result = {"downloaded": 0, "skipped": 0, "removed": 0, "errors": 0, "total": 0}

        if not self.host or not self.user or not self.password:
            logger.warning("FTP sync skipped: credentials not configured")
            return result

        remote_mods_dir = f"{self.base_dir.rstrip('/')}/mods"
        local_dir = Path(local_dir)
        local_dir.mkdir(parents=True, exist_ok=True)

        conn = None
        try:
            conn = self._get_connection()
            is_sftp = hasattr(conn, 'listdir')

            # List remote files
            if is_sftp:
                remote_files = conn.listdir(remote_mods_dir)
            else:
                conn.cwd(remote_mods_dir)
                remote_files = conn.nlst()

            result["total"] = len(remote_files)
            remote_set = set()

            for filename in remote_files:
                remote_set.add(filename)
                remote_path = f"{remote_mods_dir}/{filename}"
                local_path = local_dir / filename

                try:
                    if is_sftp:
                        remote_size = conn.stat(remote_path).st_size
                    else:
                        remote_size = conn.size(filename)
                except Exception:
                    remote_size = None

                if local_path.exists():
                    local_size = local_path.stat().st_size
                    if remote_size is not None and local_size == remote_size:
                        result["skipped"] += 1
                        continue

                logger.info("Downloading %s", filename)
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

            # Remove local files not present on remote
            for local_file in local_dir.iterdir():
                if local_file.is_file() and local_file.name not in remote_set:
                    if local_file.suffix.lower() in ('.mod', '.xm', '.s3m', '.it', '.mptm', '.stm', '.669', '.amf', '.ams', '.dbm', '.dmf', '.dsm', '.far', '.gdm', '.j2b', '.mdl', '.med', '.mtm', '.okt', '.psm', '.ptm', '.ult', '.umx', '.mt2', '.mo3'):
                        local_file.unlink()
                        result["removed"] += 1
                        logger.info("Removed stale local file %s", local_file.name)

        except Exception as exc:
            logger.error("FTP sync failed: %s", exc)
        finally:
            if conn:
                try:
                    if hasattr(conn, 'quit'):
                        conn.quit()
                    else:
                        conn.close()
                except Exception:
                    pass

        logger.info("MOD sync from FTP: %d downloaded, %d skipped, %d removed, %d errors, %d total",
                    result["downloaded"], result["skipped"], result["removed"], result["errors"], result["total"])
        return result


# Global helper function used in webhooks.py
def upload_bytes(data: bytes, rel_path: str):
    client = StorageFTPClient()
    return client.upload_bytes(data, rel_path)


# Singleton instance for webhooks.py
class FTPClientWrapper:
    """Async wrapper for StorageFTPClient for use in webhooks."""
    
    async def upload(self, local_path: Path, rel_path: str) -> Optional[str]:
        """Upload a file from local path to FTP.
        
        Args:
            local_path: Path to the local file
            rel_path: Relative path on the remote server
            
        Returns:
            The remote path if successful, None otherwise
        """
        import asyncio
        from pathlib import Path
        
        def _upload():
            client = StorageFTPClient()
            data = Path(local_path).read_bytes()
            success = client.upload_bytes(data, rel_path)
            return rel_path if success else None
        
        # Run sync FTP upload in thread pool
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _upload)


ftp_client = FTPClientWrapper()
