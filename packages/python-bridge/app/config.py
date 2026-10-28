from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    # FastAPI
    app_name: str = "Contabo Storage Manager"
    debug: bool = True

    # Local storage (mounted from host)
    files_dir: str = "/data/files"

    # MilkDrop preset directories base (milk/, milkSML/, milkMED/, milkLRG/, custom_milk/)
    presets_dir: str = "/data/files"

    # Texture directories base (textures/, weeks_textures/, custom_textures/)
    textures_dir: str = "/data/files"

    # Song directories base (songs/, weeks_songs/, flac_songs/)
    songs_dir: str = "/data/files"

    # Webhook security
    webhook_secret: Optional[str] = None
    shader_generation_token: Optional[str] = None
    image_effects_repo_dir: str = "/root/image_video_effects"
    image_effects_shader_lists_dir: str = "shader-lists"

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
    cors_origin_regex: str = (
        r"^https://([a-z0-9_-]+\.)?(1ink\.us|noahcohn\.com)$|^http://(localhost|127\.0\.0\.1)(:\d+)?$"
    )

    # Polling settings
    external_api_url: Optional[str] = None
    external_api_key: Optional[str] = None
    poll_interval_seconds: int = 60

    # External FLAC Player backend (optional)
    # When set, uploaded audio files are also registered with this API.
    flac_player_api_url: Optional[str] = None

    # Remote FLAC mirror folder (under EXTERNAL_FTP_DIR)
    external_flac_dir: str = "flac_songs"

    # FTP settings (alternative to EXTERNAL_FTP_*)
    ftp_host: Optional[str] = None
    ftp_port: int = 21
    ftp_user: Optional[str] = None
    ftp_pass: Optional[str] = None
    ftp_upload_dir: str = "/"
    ftp_tls: bool = False

    # Deploy target (storage.1ink.us or similar) - credentials stay ONLY on the VPS
    deploy_host: Optional[str] = None
    deploy_port: int = 22
    deploy_user: Optional[str] = None
    deploy_pass: Optional[str] = None
    deploy_base_dir: str = "/"
    deploy_base_dir_go: Optional[str] = None
    # Production DreamHost docroot parent for projectm.1ink.us (e.g. /home/ford442).
    # With target_folder=projectm.1ink.us this lands at /home/ford442/projectm.1ink.us/.
    deploy_base_dir_prod: Optional[str] = None
    deploy_auth_token: Optional[str] = None
    flac_player_base_url: str = "https://test.1ink.us"

    # clip-stacker processing jobs (/webhook/clip-stacker/jobs)
    # When set, job endpoints require `Authorization: Bearer <token>`. Unset keeps
    # them open like the other clip-stacker routes.
    clip_stacker_auth_token: Optional[str] = None
    clip_stacker_max_concurrent_jobs: int = 1
    # Refuse to download a job source larger than this (Drive / remote URL).
    clip_stacker_max_source_mb: int = 20480
    # Finished job folders older than this are pruned when a new job is created.
    clip_stacker_job_retention_days: int = 7
    # RIFE worker command, e.g. "python /opt/rife/inference_video.py --input {input} --output {output} --multi {multiplier}".
    # Split with shlex and run without a shell. Unset = rife jobs fail with a clear error.
    clip_stacker_rife_command: Optional[str] = None

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()


def get_settings() -> Settings:
    """Return the settings singleton."""
    return settings
