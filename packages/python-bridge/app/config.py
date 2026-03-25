"""Application settings loaded from environment / .env file."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    app_env: str = "production"

    # FTP
    ftp_host: str = "127.0.0.1"
    ftp_port: int = 21
    ftp_user: str = "ftpbridge"
    ftp_pass: str = ""
    ftp_upload_dir: str = "/home/ftpbridge/files"
    ftp_tls: bool = False

    # Webhook
    webhook_secret: str = ""
    webhook_hmac_algo: str = "sha256"

    # API
    python_host: str = "0.0.0.0"
    python_port: int = 8000
    cors_origins: str = "*"

    # Static file serving
    static_base_url: str = "http://localhost:8000/files"
    max_upload_mb: int = 512

    # Storage (local volume mount)
    files_dir: str = "/data/files"

    # Logging
    log_level: str = "info"
    log_file: str = "/var/log/ftpbridge/app.log"

    # Polling
    poll_interval_seconds: int = 60
    external_api_url: str = ""
    external_api_key: str = ""

    external_ftp_host: Optional[str] = None
    external_ftp_user: Optional[str] = None
    external_ftp_pass: Optional[str] = None
    external_ftp_port: Optional[int] = 22
    
    @property
    def cors_origins_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
