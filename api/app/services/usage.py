from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import Instance, InstanceStatus, Plan, UsageBucket, User, UserQuota


def summary(rows: list[UsageBucket]) -> dict:
    return {
        "active_seconds": sum(r.active_seconds for r in rows),
        "cpu_seconds": round(sum(float(r.cpu_seconds) for r in rows), 2),
        "mem_mb_seconds": sum(r.mem_mb_seconds for r in rows),
        "disk_bytes_max": max((r.disk_bytes_max for r in rows), default=0),
        "net_rx_bytes": sum(r.net_rx_bytes for r in rows),
        "net_tx_bytes": sum(r.net_tx_bytes for r in rows),
    }


def _bucket_label(moment: datetime, granularity: str) -> str:
    if granularity == "day" or granularity == "daily":
        return moment.strftime("%Y-%m-%d")
    return moment.strftime("%Y-%m-%dT%H:00:00Z")


def user_usage(
    db: Session,
    user_id: int,
    start: datetime,
    end: datetime,
    granularity: str = "hour",
) -> dict:
    rows = (
        db.query(UsageBucket)
        .filter(
            UsageBucket.user_id == user_id,
            UsageBucket.bucket_start >= start,
            UsageBucket.bucket_start < end,
        )
        .order_by(UsageBucket.bucket_start)
        .all()
    )
    grouped: dict[str, dict] = {}
    for row in rows:
        label = _bucket_label(row.bucket_start, granularity)
        item = grouped.setdefault(
            label,
            {
                "ts": label,
                "active_seconds": 0,
                "cpu_seconds": 0.0,
                "mem_mb_seconds": 0,
                "net_rx_bytes": 0,
                "net_tx_bytes": 0,
            },
        )
        item["active_seconds"] += row.active_seconds
        item["cpu_seconds"] += float(row.cpu_seconds)
        item["mem_mb_seconds"] += row.mem_mb_seconds
        item["net_rx_bytes"] += row.net_rx_bytes
        item["net_tx_bytes"] += row.net_tx_bytes
    for item in grouped.values():
        item["cpu_seconds"] = round(item["cpu_seconds"], 2)
    return {"summary": summary(rows), "series": list(grouped.values())}


def admin_usage_by_user(
    db: Session, start: datetime, end: datetime, metric: str = "active_seconds", limit: int = 50
) -> list[dict]:
    column = {
        "active_seconds": UsageBucket.active_seconds,
        "cpu_seconds": UsageBucket.cpu_seconds,
        "mem_mb_seconds": UsageBucket.mem_mb_seconds,
        "disk_bytes_max": UsageBucket.disk_bytes_max,
    }.get(metric, UsageBucket.active_seconds)
    rows = (
        db.query(UsageBucket.user_id, func.sum(column).label("value"))
        .filter(UsageBucket.bucket_start >= start, UsageBucket.bucket_start < end)
        .group_by(UsageBucket.user_id)
        .order_by(func.sum(column).desc())
        .limit(limit)
        .all()
    )
    user_ids = [r[0] for r in rows]
    users = (
        {u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}
    )
    return [
        {
            "user_id": r[0],
            "key": users[r[0]].username if r[0] in users else str(r[0]),
            "value": float(r[1] or 0),
        }
        for r in rows
    ]


def admin_usage_by_day(db: Session, start: datetime, end: datetime) -> list[dict]:
    rows = (
        db.query(UsageBucket)
        .filter(UsageBucket.bucket_start >= start, UsageBucket.bucket_start < end)
        .order_by(UsageBucket.bucket_start)
        .all()
    )
    grouped: dict[str, dict] = {}
    for row in rows:
        label = row.bucket_start.strftime("%Y-%m-%d")
        item = grouped.setdefault(
            label,
            {"ts": label, "active_seconds": 0, "cpu_seconds": 0.0, "mem_mb_seconds": 0},
        )
        item["active_seconds"] += row.active_seconds
        item["cpu_seconds"] += float(row.cpu_seconds)
        item["mem_mb_seconds"] += row.mem_mb_seconds
    for item in grouped.values():
        item["cpu_seconds"] = round(item["cpu_seconds"], 2)
    return list(grouped.values())


def overview(db: Session) -> dict:
    now = datetime.utcnow()
    today = datetime(now.year, now.month, now.day)
    week_ago = now - timedelta(days=7)
    running = (
        db.query(func.count(Instance.id)).filter(Instance.status == InstanceStatus.running).scalar()
        or 0
    )
    sleeping = (
        db.query(func.count(Instance.id))
        .filter(Instance.status == InstanceStatus.sleeping)
        .scalar()
        or 0
    )
    stopped = (
        db.query(func.count(Instance.id)).filter(Instance.status == InstanceStatus.stopped).scalar()
        or 0
    )
    errors = (
        db.query(func.count(Instance.id)).filter(Instance.status == InstanceStatus.error).scalar()
        or 0
    )
    today_rows = db.query(UsageBucket).filter(UsageBucket.bucket_start >= today).all()
    active_users = (
        db.query(func.count(func.distinct(UsageBucket.user_id)))
        .filter(UsageBucket.bucket_start >= week_ago, UsageBucket.active_seconds > 0)
        .scalar()
        or 0
    )
    return {
        "users": {
            "total": db.query(func.count(User.id)).filter(User.deleted_at.is_(None)).scalar() or 0,
            "active_7d": active_users,
        },
        "instances": {
            "running": running,
            "sleeping": sleeping,
            "stopped": stopped,
            "error": errors,
        },
        "today": {
            "active_hours": round(sum(r.active_seconds for r in today_rows) / 3600, 2),
            "cpu_hours": round(sum(float(r.cpu_seconds) for r in today_rows) / 3600, 2),
            "mem_gb_hours": round(sum(r.mem_mb_seconds for r in today_rows) / 1024 / 3600, 2),
        },
    }


def plans_snapshot(db: Session) -> list[dict]:
    return [
        {
            "id": p.id,
            "code": p.code,
            "name": p.name,
            "memory_mb": p.memory_mb,
            "cpu_cores": float(p.cpu_cores),
            "disk_gb": p.disk_gb,
        }
        for p in db.query(Plan).all()
    ]


def quota_remaining(db: Session, user_id: int) -> int | None:
    quota = db.get(UserQuota, user_id)
    if quota is None:
        return None
    plan = db.get(Plan, quota.plan_id) if quota.plan_id else None
    total = (plan.monthly_minutes * 60 if plan else 0) + quota.extra_seconds
    return total - quota.seconds_used
