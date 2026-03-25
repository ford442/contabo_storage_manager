import paramiko
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

class StorageFTPClient:
    """Handles uploading files from the bridge to external FTP/SFTP storage."""

    def __init__(self):
        self.client = None
        self.is_sftp = True  # Default to secure SFTP

    def connect(self) -> bool:
        """Connect using env variables."""
        host = settings.external_ftp_host
        user = settings.external_ftp_user
        password = settings.external_ftp_pass
        port = settings.external_ftp_port or (22 if self.is_sftp else 21)

        if not host or not user or not password:
            logger.warning("External FTP not configured - skipping upload")
            return False

        try:
            if self.is_sftp:
                transport = paramiko.Transport((host, port))
                transport.connect(username=user, password=password)
                self.client = paramiko.SFTPClient.from_transport(transport)
            else:
                # Plain FTP fallback (less recommended)
                self.client = paramiko.SSHClient()
                self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                self.client.connect(host, port=port, username=user, password=password)
            logger.info(f"Connected to external FTP: {host}")
            return True
        except Exception as e:
            logger.error(f"FTP connection failed: {e}")
            return False

    async def upload(self, local_path: str | Path, remote_rel_path: str) -> Optional[str]:
        """Upload file and return remote path if successful."""
        if not self.connect():
            return None

        try:
            remote_full = f"/{remote_rel_path.lstrip('/')}"
            remote_dir = str(Path(remote_full).parent)

            # Create remote directory if it doesn't exist
            try:
                if self.is_sftp:
                    self.client.mkdir(remote_dir)
            except:
                pass  # Directory may already exist

            if self.is_sftp:
                self.client.put(str(local_path), remote_full)
            else:
                # Plain FTP logic (simplified)
                sftp = self.client.open_sftp()
                sftp.put(str(local_path), remote_full)
                sftp.close()

            logger.info(f"Uploaded: {local_path} → {remote_full}")
            return remote_rel_path

        except Exception as e:
            logger.error(f"Upload failed for {local_path}: {e}")
            return None
        finally:
            if self.client:
                try:
                    self.client.close()
                except:
                    pass


# Global instance
ftp_client = StorageFTPClient()
