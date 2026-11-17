from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from ..models import Plan, User, UserQuota

TZ = ZoneInfo("Asia/Shanghai")


def current_cycle(now: datetime | None = None) -> tuple[date, date]:
    now = now or datetime.now(UTC)
    local = now.astimezone(TZ)
    start = date(local.year, local.month, 1)
    if local.month == 12:
        end = date(local.year + 1, 1, 1)
    else:
        end = date(local.year, local.month + 1, 1)
    return start, end


def ensure_quota(db: Session, user: User, plan: Plan | None = None) -> UserQuota:
    quota = db.get(UserQuota, user.id)
    if quota is not None:
        return quota
    plan = plan or user.plan or db.query(Plan).filter(Plan.is_default.is_(True)).first()
    start, end = current_cycle()
    quota = UserQuota(
        user_id=user.id,
        plan_id=plan.id if plan else None,
        cycle_start=start,
        cycle_end=end,
        extra_seconds=0,
        seconds_used=0,
    )
    db.add(quota)
    db.flush()
    return quota


def effective(quota: UserQuota, plan: Plan | None) -> dict:
    cpu = (
        quota.cpu_cores_override
        if quota.cpu_cores_override is not None
        else (plan.cpu_cores if plan else 1)
    )
    memory = (
        quota.memory_mb_override
        if quota.memory_mb_override is not None
        else (plan.memory_mb if plan else 2048)
    )
    disk = (
        quota.disk_gb_override
        if quota.disk_gb_override is not None
        else (plan.disk_gb if plan else 20)
    )
    minutes = (
        quota.monthly_minutes_override
        if quota.monthly_minutes_override is not None
        else (plan.monthly_minutes if plan else 0)
    )
    total_seconds = int(minutes) * 60 + int(quota.extra_seconds)
    remaining = max(0, total_seconds - int(quota.seconds_used))
    return {
        "cpu_cores": float(cpu),
        "memory_mb": int(memory),
        "disk_gb": int(disk),
        "monthly_minutes": int(minutes),
        "extra_minutes": int(quota.extra_seconds // 60),
        "used_minutes": int(quota.seconds_used // 60),
        "remaining_minutes": int(remaining // 60),
        "total_minutes": int(total_seconds // 60),
        "cycle_start": quota.cycle_start.isoformat(),
        "cycle_end": quota.cycle_end.isoformat(),
        "max_snapshots": plan.max_snapshots if plan else 0,
        "max_user_images": plan.max_user_images if plan else 0,
    }


def quota_view(db: Session, user: User) -> dict:
    quota = ensure_quota(db, user)
    plan = db.get(Plan, quota.plan_id) if quota.plan_id else user.plan
    view = effective(quota, plan)
    view["plan"] = plan.name if plan else None
    view["plan_code"] = plan.code if plan else None
    return view
