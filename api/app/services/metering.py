from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..config import settings
from ..models import UsageBucket, UserQuota
from ..redis import get_redis

log = logging.getLogger("dsh.metering")


def hour_key(moment: datetime | None = None) -> str:
    moment = moment or datetime.now(UTC)
    return moment.strftime("%Y%m%d%H")


def _key(instance_id: int, hour: str) -> str:
    return f"usage:{instance_id}:{hour}"


def touch(instance_id: int) -> None:
    try:
        get_redis().set(
            f"inst:last_active:{instance_id}", int(datetime.now(UTC).timestamp()), ex=7200
        )
    except Exception:
        pass


def last_active(instance_id: int) -> datetime | None:
    try:
        raw = get_redis().get(f"inst:last_active:{instance_id}")
    except Exception:
        return None
    if not raw:
        return None
    return datetime.fromtimestamp(int(raw), tz=UTC).replace(tzinfo=None)


def record(
    instance_id: int,
    user_id: int,
    stats: dict,
    interval: int,
    *,
    active: bool,
    net_rx_delta: int = 0,
    net_tx_delta: int = 0,
) -> None:
    if not settings.metering_enabled:
        return
    key = _key(instance_id, hour_key())
    cpu_seconds = max(0.0, float(stats.get("cpu_percent", 0))) / 100.0 * interval
    mem_mb = max(0, int(stats.get("mem_used_bytes", 0))) / (1024 * 1024)
    try:
        redis = get_redis()
        pipe = redis.pipeline()
        pipe.hset(key, mapping={"user_id": user_id})
        pipe.hincrbyfloat(key, "cpu_seconds", round(cpu_seconds, 4))
        pipe.hincrby(key, "mem_mb_seconds", int(mem_mb * interval))
        pipe.hincrby(key, "net_rx_bytes", max(0, net_rx_delta))
        pipe.hincrby(key, "net_tx_bytes", max(0, net_tx_delta))
        pipe.hincrby(key, "samples", 1)
        if active:
            pipe.hincrby(key, "active_seconds", interval)
        pipe.expire(key, 7200)
        pipe.execute()
    except Exception:
        log.warning("metering record failed for instance %s", instance_id, exc_info=True)


def flush(db: Session) -> dict:
    """Move Redis counters into hourly DB buckets. Returns aggregate per user."""
    try:
        redis = get_redis()
        keys = list(redis.scan_iter(match="usage:*", count=200))
    except Exception:
        return {"buckets": 0, "users": {}}
    if not keys:
        return {"buckets": 0, "users": {}}

    touched_users: dict[int, int] = {}
    count = 0
    for key in keys:
        parts = key.split(":")
        if len(parts) != 3:
            continue
        try:
            instance_id = int(parts[1])
            bucket_start = datetime.strptime(parts[2], "%Y%m%d%H")
        except ValueError:
            continue
        data = redis.hgetall(key)
        if not data:
            redis.delete(key)
            continue
        user_id = int(data.get("user_id", 0))
        active = int(data.get("active_seconds", 0))
        row = (
            db.query(UsageBucket)
            .filter(
                UsageBucket.instance_id == instance_id,
                UsageBucket.bucket_start == bucket_start,
            )
            .first()
        )
        if row is None:
            row = UsageBucket(
                instance_id=instance_id,
                user_id=user_id,
                bucket_start=bucket_start,
                active_seconds=0,
                cpu_seconds=0,
                mem_mb_seconds=0,
                net_rx_bytes=0,
                net_tx_bytes=0,
                samples=0,
            )
            db.add(row)
        row.active_seconds += active
        row.cpu_seconds = float(row.cpu_seconds) + float(data.get("cpu_seconds", 0))
        row.mem_mb_seconds += int(data.get("mem_mb_seconds", 0))
        row.net_rx_bytes += int(data.get("net_rx_bytes", 0))
        row.net_tx_bytes += int(data.get("net_tx_bytes", 0))
        row.samples += int(data.get("samples", 0))
        if active:
            db.query(UserQuota).filter(UserQuota.user_id == user_id).update(
                {UserQuota.seconds_used: UserQuota.seconds_used + active}
            )
            touched_users[user_id] = touched_users.get(user_id, 0) + active
        count += 1

    db.commit()
    try:
        for key in keys:
            redis.delete(key)
    except Exception:
        log.warning("could not clear flushed usage counters", exc_info=True)
    return {"buckets": count, "users": touched_users}
