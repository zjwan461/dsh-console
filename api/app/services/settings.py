from __future__ import annotations

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Setting

# Keys whose deployment-level default comes from the environment. The database
# still wins once an admin saves a value; these are only the seed/fallback.
ENV_BACKED = (
    "base_domain",
    "console_name",
    "dsh_image_repository",
    "default_idle_minutes",
    "upload_max_mb",
)

# Values that were hardcoded before they were env-backed. A stored value equal to
# one of these means "never customised": a fresh database seeded it from the old
# constant, so the env value has to win or `CONSOLE_BASE_DOMAIN` would be inert.
LEGACY_DEFAULTS: dict[str, object] = {
    "base_domain": "dsh.example.com",
}


def default_settings() -> dict[str, object]:
    """Deployment defaults, read from the environment at call time."""
    return {
        "base_domain": settings.base_domain,
        "console_name": settings.console_name,
        "default_plan_id": None,
        "default_image_id": None,
        # Registry the console watches for new dsh builds (host/owner/repo).
        "dsh_image_repository": settings.dsh_image_repository,
        "default_idle_minutes": settings.default_idle_minutes,
        "allow_user_version_switch": True,
        "allow_user_self_key": True,
        "snapshot_retention_days": 30,
        "deleted_user_retention_days": 7,
        "upload_max_mb": settings.upload_max_mb,
        "admin_terminal_enabled": True,
        "maintenance_mode": False,
        "maintenance_message": "",
    }


def all_settings(db: Session) -> dict:
    merged = default_settings()
    for row in db.query(Setting).all():
        merged[row.k] = row.v
    return merged


def get_setting(db: Session, key: str, default=None):
    row = db.get(Setting, key)
    if row is not None:
        return row.v
    return default_settings().get(key, default)


def put_setting(db: Session, key: str, value, actor_id: int | None) -> None:
    row = db.get(Setting, key)
    if row is None:
        row = Setting(k=key, v=value, updated_by=actor_id)
        db.add(row)
    else:
        row.v = value
        row.updated_by = actor_id


def adopt_env_defaults(db: Session, keys: tuple[str, ...] = ENV_BACKED) -> list[str]:
    """Let the environment take over values that were never intentionally set.

    Databases created before the defaults were env-backed hold the old hardcoded
    constants. Without this, `CONSOLE_BASE_DOMAIN` stays ignored forever and
    Caddy keeps issuing certificates for `dsh.example.com`.
    """
    changed: list[str] = []
    defaults = default_settings()
    for key in keys:
        row = db.get(Setting, key)
        if row is None:
            continue
        env_value = defaults.get(key)
        if env_value in (None, ""):
            continue
        if row.v == env_value:
            continue
        if row.v != LEGACY_DEFAULTS.get(key):
            continue  # someone chose this value on purpose; leave it alone
        row.v = env_value
        changed.append(key)
    if changed:
        db.commit()
    return changed