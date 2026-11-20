from __future__ import annotations

from functools import lru_cache
from typing import Any

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Every deployment-specific value is an env var."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

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

    # `CONSOLE_BASE_DOMAIN` is what `.env.example` and compose.yaml set, but the
    # field name would otherwise only answer to `BASE_DOMAIN` — the documented
    # variable was silently ignored and Caddy kept using the default domain.
    base_domain: str = Field(
        default="dsh.example.com",
        validation_alias=AliasChoices("CONSOLE_BASE_DOMAIN", "BASE_DOMAIN"),
    )
    console_name: str = "优墨云舱"
    # Public port the console is reached on when it is not the scheme default
    # (a test host on :38080, for instance). Instance URLs, the /go redirect and
    # DSH's trusted-host fence all have to carry it: the browser sends
    # `Host: sub.domain:38080` and DSH rejects an authority it does not trust.
    public_port: int | None = None

    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = ""

    login_rate_per_minute: int = 5
    login_rate_per_hour: int = 10

    upload_max_mb: int = 1024
    default_idle_minutes: int = 30

    # ── container runtime (M2) ───────────────────────────────────────────
    data_root: str = "/srv/dsh-console/data"
    # Absolute path of DATA_ROOT on the Docker HOST. Bind sources are resolved by
    # the daemon, not by this container, so they must use the host path. Equal to
    # data_root when the project really lives at /srv/dsh-console.
    host_data_root: str = "/srv/dsh-console/data"
    docker_host: str = "unix:///var/run/docker.sock"
    docker_fake: bool = False
    # Default HTTP timeout for daemon calls, and longer budgets for the two
    # operations that legitimately take minutes on multi-GB images.
    docker_timeout: int = 300
    docker_commit_timeout: int = 1800
    docker_pull_timeout: int = 3600
    # How long to wait for a freshly created instance to report healthy. The
    # first boot of a dsh image composes its profile and can take minutes on a
    # small host; giving up too early used to mark a perfectly good instance as
    # failed. A timeout is not fatal either — reconcile re-probes and recovers.
    instance_health_timeout: int = 300

    # ── published image catalogue (M6) ───────────────────────────────────
    # The console watches this repository for new dsh builds. It is a registry
    # reference (host/owner/repo), not a docker image on the local host.
    dsh_image_repository: str = "ghcr.io/zjwan461/dsh-docker"
    # Which platform manifest to inspect for size/created metadata.
    dsh_image_platform: str = "amd64"
    # Credentials for a private package; empty means anonymous (public packages).
    registry_username: str = ""
    registry_password: str = ""
    # Explicit proxy for registry access. Empty means direct: the probe ignores
    # the ambient HTTP_PROXY/NO_PROXY of the host it runs on.
    registry_proxy: str = ""
    registry_check_enabled: bool = True
    registry_check_interval_hours: int = 6
    # Upload limit for `docker save` tarballs (they are gigabytes).
    image_upload_max_mb: int = 8192
    instance_network: str = "dsh-edge"
    instance_uid: int = 1000
    instance_gid: int = 1000
    instance_shm_mb: int = 512
    instance_pids_limit: int = 4096
    public_scheme: str = "https"
    # Caddy's listener. Unset => derived from public_scheme (443 / 80), so the two
    # can never disagree.
    caddy_listen_port: int | None = None
    caddy_admin_url: str = "http://console-web:2019"
    caddy_enabled: bool = True
    caddy_access_log_enabled: bool = True
    caddy_log_listen: str = "0.0.0.0"
    caddy_log_port: int = 9001
    jobs_inline: bool = False
    # Close an idle admin terminal after this many seconds (input resets it).
    terminal_idle_seconds: int = 900

    # ── metering / worker (M3) ───────────────────────────────────────────
    metering_enabled: bool = True
    stats_interval_seconds: int = 30
    usage_flush_seconds: int = 60
    idle_check_seconds: int = 60
    node_metrics_enabled: bool = True
    node_metrics_interval: int = 30
    node_metrics_retention_days: int = 7
    active_network_threshold_bytes: int = 65536

    # capacity admission (see docs/02 §10)
    memory_reserve_mb: int = 4096
    memory_reserve_percent: int = 10
    cpu_overcommit: float = 4.0
    disk_use_ratio: float = 0.90

    cors_origins: list[str] = Field(default_factory=list)

    @field_validator("public_port", "caddy_listen_port", mode="before")
    @classmethod
    def _blank_optional_int_is_none(cls, value: Any) -> Any:
        """Treat an empty env var as "not set".

        docker compose passes `${VAR:-}` through as an empty string when the
        variable is absent, and pydantic would otherwise fail to parse it as an
        int — which crashed the API on boot, before it could even report why.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def is_dev(self) -> bool:
        return self.env in {"dev", "test"}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
