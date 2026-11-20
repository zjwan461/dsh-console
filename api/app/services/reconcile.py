from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy.orm import Session

from ..drivers import DriverError, get_driver
from ..models import Instance, InstanceStatus
from ..security import utcnow
from . import caddy
from .instances import log_event

log = logging.getLogger("dsh.reconcile")

STUCK_AFTER = timedelta(minutes=15)


def reconcile(db: Session) -> dict:
    driver = get_driver()
    fixed_errors = 0
    recovered = 0
    checked = 0
    for instance in db.query(Instance).filter(Instance.status != InstanceStatus.deleted).all():
        checked += 1
        if instance.status == InstanceStatus.running:
            if not instance.container_id:
                instance.status = InstanceStatus.error
                instance.error = "container id missing"
                instance.health = "unknown"
                log_event(db, instance, "reconcile", InstanceStatus.running, InstanceStatus.error)
                fixed_errors += 1
                continue
            try:
                info = driver.inspect(instance.container_id)
            except DriverError:
                instance.status = InstanceStatus.error
                instance.error = "container not found"
                instance.health = "unknown"
                log_event(db, instance, "reconcile", InstanceStatus.running, InstanceStatus.error)
                fixed_errors += 1
                continue
            if info.status in {"exited", "dead"}:
                instance.status = InstanceStatus.error
                instance.error = f"container {info.status}"
                instance.health = "unhealthy"
                log_event(db, instance, "reconcile", InstanceStatus.running, InstanceStatus.error)
                fixed_errors += 1
        elif instance.status in {
            InstanceStatus.provisioning,
            InstanceStatus.starting,
            InstanceStatus.stopping,
            InstanceStatus.deleting,
        }:
            if instance.updated_at and utcnow() - instance.updated_at > STUCK_AFTER:
                instance.status = InstanceStatus.error
                instance.error = "operation timed out"
                log_event(db, instance, "reconcile_timeout", None, InstanceStatus.error)
                fixed_errors += 1
        elif instance.status == InstanceStatus.error and instance.container_id:
            # `error` is not necessarily terminal: the health wait gives up while
            # dsh is still composing its profile on a slow first boot, and the
            # container often comes up fine seconds later. Re-probe instead of
            # leaving a working instance marked as failed.
            try:
                info = driver.inspect(instance.container_id)
            except DriverError:
                continue
            healthy = info.status == "running" and info.health in {None, "healthy"}
            if not healthy:
                continue
            previous = instance.status
            instance.status = InstanceStatus.running
            instance.health = "healthy"
            instance.error = None
            instance.status_reason = None
            instance.last_started_at = info.started_at or utcnow()
            log_event(db, instance, "recovered", previous, InstanceStatus.running, "容器已自愈")
            recovered += 1
    db.commit()
    caddy_error = None
    try:
        caddy.apply(db)
    except caddy.CaddyError as exc:
        caddy_error = str(exc)
        log.warning("reconcile could not apply caddy config: %s", exc)
    return {
        "checked": checked,
        "fixed": fixed_errors,
        "recovered": recovered,
        "caddy_error": caddy_error,
    }
