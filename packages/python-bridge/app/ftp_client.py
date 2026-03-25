import ftplib
import ssl
from pathlib import Path
from typing import Optional
from .config import settings
from .logger import get_logger

logger = get_logger("ftp_client")

class StorageFTPClient:
    def __init__(self):
        self.host = settings.external_ftp_host
        self.user = settings.external_ftp_user
        self.password = settings.external_ftp_pass
        self.port = settings.external_ftp_port or 21
        self.base_dir = settings.external_ftp_dir or "/"

    def _get_connection(self) -> ftplib.FTP_TLS:
        """Create a secure FTP_TLS connection."""
        ftp = ftplib.FTP_TLS()
        ftp.connect(self.host, self.port, timeout=30)
        ftp.login(self.user, self.password)
        ftp.prot_p()  # Switch to secure data connection
        return ftp

    def _ensure_remote_dir(self, ftp: ftplib.FTP_TLS, rel_path: str):
        """Recursively create directories on the FTP server."""
        parts = rel_path.strip("/").split("/")
        current_path = self.base_dir.rstrip("/")
        
        for part in parts:
            current_path = f"{current_path}/{part}"
            try:
                ftp.cwd(current_path)
            except ftplib.error_perm:
                logger.info(f"Creating remote directory: {current_path}")
                ftp.mkd(current_path)

    def upload_bytes(self, data: bytes, remote_rel_path: str) -> bool:
        """Upload raw bytes to the remote FTP server."""
        if not self.host or not self.user:
            return False

        ftp = None
        try:
            ftp = self._get_connection()
            
            # 1. Ensure the subfolders exist (e.g., image-effects/shaders)
            remote_path_obj = Path(remote_rel_path)
            if remote_path_obj.parent != Path("."):
                self._ensure_remote_dir(ftp, str(remote_path_obj.parent))

            # 2. Upload the file
            target_file = f"{self.base_dir.rstrip('/')}/{remote_rel_path.lstrip('/')}"
            
            # We use a BytesIO wrapper to send raw bytes via storbinary
            import io
            bio = io.BytesIO(data)
            ftp.storbinary(f"STOR {target_file}", bio)
            
            logger.info(f"Successfully bridged file to DreamHost: {target_file}")
            return True

        except Exception as e:
            logger.error(f"DreamHost FTP Bridge failed: {e}")
            return False
        finally:
            if ftp:
                try:
                    ftp.quit()
                except:
                    pass

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
