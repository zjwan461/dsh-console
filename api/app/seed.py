from __future__ import annotations

import logging
import secrets

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import settings
from .models import Plan, Role, Setting, User, UserQuota, UserStatus
from .security import hash_password, validate_username
from .services.quota import current_cycle
from .services.settings import default_settings

log = logging.getLogger("dsh.seed")

DEFAULT_PLANS = [
    {
        "code": "basic",
        "name": "基础",
        "description": "个人轻量使用",
        "cpu_cores": 1,
        "memory_mb": 2048,
        "disk_gb": 20,
        "monthly_minutes": 3600,
        "max_snapshots": 2,
        "max_user_images": 2,
        "is_default": True,
        "sort_order": 10,
    },
    {
        "code": "standard",
        "name": "标准",
        "description": "日常开发与文档",
        "cpu_cores": 2,
        "memory_mb": 4096,
        "disk_gb": 50,
        "monthly_minutes": 7200,
        "max_snapshots": 4,
        "max_user_images": 4,
        "is_default": False,
        "sort_order": 20,
    },
    {
        "code": "performance",
        "name": "高性能",
        "description": "视频、编译与重负载",
        "cpu_cores": 4,
        "memory_mb": 8192,
        "disk_gb": 100,
        "monthly_minutes": 14400,
        "max_snapshots": 8,
        "max_user_images": 8,
        "is_default": False,
        "sort_order": 30,
    },
]


def seed_plans(db: Session) -> None:
    if db.query(Plan).count() > 0:
        return
    for item in DEFAULT_PLANS:
        db.add(Plan(**item))
    try:
        db.commit()
    except IntegrityError:
        # Another API worker won the race; its rows are equivalent.
        db.rollback()
        return
    log.info("seeded %d plans", len(DEFAULT_PLANS))


def seed_settings(db: Session) -> None:
    for key, value in default_settings().items():
        if db.get(Setting, key) is None:
            db.add(Setting(k=key, v=value))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()


def seed_admin(db: Session) -> None:
    if db.query(User.id).first() is not None:
        return
    username = settings.bootstrap_admin_username.strip() or "admin"
    if validate_username(username) is not None:
        username = "admin"
    password = settings.bootstrap_admin_password
    generated = False
    if not password:
        password = secrets.token_urlsafe(12)
        generated = True
    plan = db.query(Plan).filter(Plan.is_default.is_(True)).first()
    start, end = current_cycle()
    user = User(
        username=username,
        subdomain=username,
        display_name="管理员",
        password_hash=hash_password(password),
        role=Role.admin,
        status=UserStatus.active,
        plan_id=plan.id if plan else None,
    )
    db.add(user)
    try:
        db.flush()
        db.add(
            UserQuota(
                user_id=user.id,
                plan_id=plan.id if plan else None,
                cycle_start=start,
                cycle_end=end,
            )
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        return
    if generated:
        log.warning(
            "BOOTSTRAP_ADMIN_PASSWORD is empty; generated admin %r password: %s "
            "(change it after first login)",
            username,
            password,
        )
    else:
        log.info("bootstrap admin %r created", username)


def seed_all(db: Session) -> None:
    seed_plans(db)
    seed_settings(db)
    seed_admin(db)
