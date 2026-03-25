from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    # FastAPI
    app_name: str = "Contabo Storage Manager"
    debug: bool = True

    # Local storage (mounted from host)
    files_dir: str = "/data/files"

    # Webhook security
    webhook_secret: Optional[str] = None

    # Static file serving
    static_base_url: str = "http://localhost:8000/files"
    max_upload_mb: int = 512

    # External SFTP/FTP (used by paramiko)
    external_ftp_host: Optional[str] = None
    external_ftp_user: Optional[str] = None
    external_ftp_pass: Optional[str] = None
    external_ftp_port: Optional[int] = 22

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()


def get_settings() -> Settings:
    """Return the settings singleton."""
    return settings
