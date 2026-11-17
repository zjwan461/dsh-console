from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import Setting

DEFAULT_SETTINGS: dict[str, object] = {
    "base_domain": "dsh.example.com",
    "console_name": "优墨云舱",
    "default_plan_id": None,
    "default_image_id": None,
    # Registry the console watches for new dsh builds (host/owner/repo).
    "dsh_image_repository": "ghcr.io/zjwan461/dsh-docker",
    "default_idle_minutes": 30,
    "allow_user_version_switch": True,
    "allow_user_self_key": True,
    "snapshot_retention_days": 30,
    "deleted_user_retention_days": 7,
    "upload_max_mb": 1024,
    "admin_terminal_enabled": True,
    "maintenance_mode": False,
    "maintenance_message": "",
}


def all_settings(db: Session) -> dict:
    merged = dict(DEFAULT_SETTINGS)
    for row in db.query(Setting).all():
        merged[row.k] = row.v
    return merged


def get_setting(db: Session, key: str, default=None):
    row = db.get(Setting, key)
    return row.v if row is not None else DEFAULT_SETTINGS.get(key, default)


def put_setting(db: Session, key: str, value, actor_id: int | None) -> None:
    row = db.get(Setting, key)
    if row is None:
        row = Setting(k=key, v=value, updated_by=actor_id)
        db.add(row)
    else:
        row.v = value
        row.updated_by = actor_id
