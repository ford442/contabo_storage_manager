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
    static_base_url: str = "https://storage.noahcohn.com/files"
    max_upload_mb: int = 512

    # Logging
    log_level: str = "info"
    log_file: str = "/var/log/ftpbridge/app.log"

    # External SFTP/FTP (used by paramiko)
    external_ftp_host: Optional[str] = None
    external_ftp_user: Optional[str] = None
    external_ftp_pass: Optional[str] = None
    external_ftp_port: Optional[int] = 22
    external_ftp_dir: Optional[str] = "/"

    # CORS
    cors_origins: str = "https://code.noahcohn.com,https://storage.noahcohn.com,https://noahcohn.com,https://test.1ink.us,http://localhost:3000,http://localhost:5173,http://localhost:8000"

    # Polling settings
    external_api_url: Optional[str] = None
    external_api_key: Optional[str] = None
    poll_interval_seconds: int = 60

    # FTP settings (alternative to EXTERNAL_FTP_*)
    ftp_host: Optional[str] = None
    ftp_port: int = 21
    ftp_user: Optional[str] = None
    ftp_pass: Optional[str] = None
    ftp_upload_dir: str = "/"
    ftp_tls: bool = False

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()


def get_settings() -> Settings:
    """Return the settings singleton."""
    return settings
