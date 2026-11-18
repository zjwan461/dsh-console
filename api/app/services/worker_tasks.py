from __future__ import annotations

import logging
from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from ..config import settings
from ..drivers import DriverError, get_driver
from ..models import (
    Instance,
    InstanceStatus,
    LedgerReason,
    NodeMetric,
    QuotaLedger,
    UserQuota,
)
from ..redis import get_redis
from . import metering
from .instances import stop_instance
from .quota import current_cycle
from .reconcile import reconcile
from .usage import quota_remaining

log = logging.getLogger("dsh.worker")


def sample_once(db: Session) -> int:
    """Sample docker stats for every running instance and feed the counters."""
    redis = get_redis()
    driver = get_driver()
    count = 0
    now = datetime.now(UTC)
    for instance in db.query(Instance).filter(Instance.status == InstanceStatus.running).all():
        if not instance.container_id:
            continue
        try:
            stats = driver.stats(instance.container_id)
        except DriverError:
            continue
        try:
            previous = redis.hgetall(f"inst:net:{instance.id}")
        except Exception:
            previous = {}
        rx, tx = int(stats.get("net_rx_bytes", 0)), int(stats.get("net_tx_bytes", 0))
        if previous:
            rx_delta = max(0, rx - int(previous.get("rx", 0)))
            tx_delta = max(0, tx - int(previous.get("tx", 0)))
        else:
            rx_delta = tx_delta = 0
        try:
            redis.hset(f"inst:net:{instance.id}", mapping={"rx": rx, "tx": tx})
            redis.expire(f"inst:net:{instance.id}", 3600)
        except Exception:
            pass

        last = metering.last_active(instance.id) or instance.last_active_at
        recently_active = bool(last and (now.replace(tzinfo=None) - last).total_seconds() <= 90)
        network_active = (rx_delta + tx_delta) >= settings.active_network_threshold_bytes
        active = recently_active or network_active
        if active:
            metering.touch(instance.id)
            instance.last_active_at = now.replace(tzinfo=None)
        metering.record(
            instance.id,
            instance.user_id,
            stats,
            settings.stats_interval_seconds,
            active=active,
            net_rx_delta=rx_delta,
            net_tx_delta=tx_delta,
        )
        count += 1
    db.commit()
    return count


def idle_once(db: Session) -> int:
    """Sleep instances whose idle timeout elapsed and that have no traffic."""
    now = datetime.now(UTC).replace(tzinfo=None)
    slept = 0
    for instance in db.query(Instance).filter(Instance.status == InstanceStatus.running).all():
        if not instance.idle_timeout_seconds:
            continue
        last = (
            metering.last_active(instance.id) or instance.last_active_at or instance.last_started_at
        )
        if last is None:
            continue
        if (now - last).total_seconds() >= instance.idle_timeout_seconds:
            log.info("sleeping idle instance %s (idle since %s)", instance.id, last)
            stop_instance(instance.id, sleep=True, reason="idle_timeout")
            slept += 1
    return slept


def quota_once(db: Session) -> int:
    """Stop instances whose owner has exhausted the monthly allowance."""
    stopped = 0
    for quota in db.query(UserQuota).all():
        remaining = quota_remaining(db, quota.user_id)
        if remaining is None:
            continue
        if remaining <= 0:
            instance = (
                db.query(Instance)
                .filter(
                    Instance.user_id == quota.user_id,
                    Instance.status.in_([InstanceStatus.running, InstanceStatus.starting]),
                )
                .first()
            )
            if instance is not None:
                log.info("stopping instance %s: quota exhausted", instance.id)
                stop_instance(instance.id, reason="quota_exhausted")
                stopped += 1
            try:
                get_redis().set(f"quota:alert:{quota.user_id}", "exhausted", ex=86400)
            except Exception:
                pass
        elif remaining <= 300:
            try:
                get_redis().set(f"quota:alert:{quota.user_id}", "low", ex=86400)
            except Exception:
                pass
    return stopped


def reset_cycles(db: Session) -> int:
    """Roll over monthly quotas whose cycle ended."""
    today = date.today()
    changed = 0
    for quota in db.query(UserQuota).filter(UserQuota.cycle_end <= today).all():
        start, end = current_cycle()
        quota.seconds_used = 0
        quota.cycle_start = start
        quota.cycle_end = end
        db.add(
            QuotaLedger(
                user_id=quota.user_id,
                delta_seconds=0,
                reason=LedgerReason.reset,
                balance_after=quota.extra_seconds,
                note=f"cycle reset {start}..{end}",
            )
        )
        changed += 1
    if changed:
        db.commit()
    return changed


def node_once(db: Session) -> NodeMetric | None:
    from . import node_metrics

    return node_metrics.collect(db)


def reconcile_once(db: Session) -> dict:
    return reconcile(db)


def registry_once(db: Session) -> dict:
    """Watch the upstream image repository for new dsh builds."""
    from . import image_catalog

    return image_catalog.check_once(db)


def skills_once(db: Session) -> dict:
    """Repair skill materialisation drift on every live instance."""
    from . import skills

    return skills.sync_all_instances(db)


def plugins_once(db: Session) -> dict:
    """Repair plugin materialisation drift on every live instance."""
    from . import plugins

    return plugins.sync_all_instances(db)


def flush_once(db: Session) -> dict:
    return metering.flush(db)
