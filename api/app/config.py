from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Every deployment-specific value is an env var."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "DSH Console"
    env: str = "dev"
    api_prefix: str = "/api/v1"
    log_level: str = "INFO"

    database_url: str = "mysql+pymysql://dsh:dsh@mysql:3306/dsh_console?charset=utf8mb4"
    redis_url: str = "redis://redis:6379/0"
    # Local development without Redis: use an in-process fake (never in production).
    redis_fake: bool = False

    # 32-byte base64url. Rotating it logs everyone out.
    session_secret: str = "dev-insecure-session-secret-change-me"
    # 32-byte base64. Encrypts model API keys; losing it makes them unreadable.
    master_key: str = ""
    cookie_name: str = "dshc_sid"
    cookie_secure: bool = False
    session_ttl_days: int = 7

    base_domain: str = "dsh.example.com"
    console_name: str = "优墨云舱"

    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = ""

    login_rate_per_minute: int = 5
    login_rate_per_hour: int = 10

    upload_max_mb: int = 1024
    default_idle_minutes: int = 30

    # capacity admission (see docs/02 §10)
    memory_reserve_mb: int = 4096
    memory_reserve_percent: int = 10
    cpu_overcommit: float = 4.0
    disk_use_ratio: float = 0.90

    cors_origins: list[str] = Field(default_factory=list)

    @property
    def is_dev(self) -> bool:
        return self.env in {"dev", "test"}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
