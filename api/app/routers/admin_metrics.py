from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import csrf_guard, require_admin
from ..drivers import DriverError, get_driver
from ..errors import ApiError, envelope
from ..models import Instance, InstanceStatus, NodeMetric, User
from ..services import usage as usage_service
from ..services.capacity import probe_capacity
from ..services.node_metrics import series as node_series
from ..services.usage import admin_usage_by_day, admin_usage_by_user

router = APIRouter(
    prefix="/admin/metrics", tags=["admin:metrics"], dependencies=[Depends(csrf_guard)]
)


def _range(start: str | None, end: str | None, days: int = 7) -> tuple[datetime, datetime]:
    now = datetime.utcnow()
    try:
        start_dt = (
            datetime.fromisoformat(start.replace("Z", "+00:00")).replace(tzinfo=None)
            if start
            else now - timedelta(days=days)
        )
        end_dt = (
            datetime.fromisoformat(end.replace("Z", "+00:00")).replace(tzinfo=None) if end else now
        )
    except ValueError:
        raise ApiError("VALIDATION_ERROR", "时间格式不正确", 422) from None
    if end_dt <= start_dt:
        raise ApiError("VALIDATION_ERROR", "结束时间必须晚于开始时间", 422)
    return start_dt, end_dt


@router.get("/overview")
def overview(
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    data = usage_service.overview(db)
    data["capacity"] = probe_capacity(db)
    latest = db.query(NodeMetric).order_by(NodeMetric.ts.desc()).first()
    data["host"] = (
        {
            "cpu_percent": float(latest.cpu_percent),
            "mem_used_percent": round(latest.mem_used_bytes / latest.mem_total_bytes * 100, 1)
            if latest.mem_total_bytes
            else 0,
            "load1": float(latest.load1),
            "containers_running": latest.containers_running,
            "at": latest.ts.isoformat() + "Z",
        }
        if latest
        else None
    )
    return envelope(request, data)


@router.get("/usage")
def usage(
    request: Request,
    start: str | None = Query(default=None, alias="from"),
    end: str | None = Query(default=None, alias="to"),
    group_by: str = Query(default="day", pattern="^(user|day)$"),
    metric: str = Query(default="active_seconds"),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    start_dt, end_dt = _range(start, end, 30)
    if group_by == "user":
        rows = admin_usage_by_user(db, start_dt, end_dt, metric)
        return envelope(request, {"rows": rows})
    return envelope(request, {"series": admin_usage_by_day(db, start_dt, end_dt)})


@router.get("/node")
def node(
    request: Request,
    start: str | None = Query(default=None, alias="from"),
    end: str | None = Query(default=None, alias="to"),
    step: int = Query(default=30, ge=15, le=3600),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    start_dt, end_dt = _range(start, end, 1)
    return envelope(request, {"series": node_series(db, start_dt, end_dt, step)})


@router.get("/instances/live")
def instances_live(
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rows = []
    driver = get_driver()
    for instance in db.query(Instance).filter(Instance.status == InstanceStatus.running).all():
        if not instance.container_id:
            continue
        try:
            stats = driver.stats(instance.container_id)
        except DriverError:
            continue
        rows.append(
            {
                "instance_id": instance.id,
                "user_id": instance.user_id,
                "cpu_percent": stats.get("cpu_percent", 0),
                "mem_used_bytes": stats.get("mem_used_bytes", 0),
                "mem_limit_bytes": stats.get("mem_limit_bytes", 0),
            }
        )
    return envelope(request, {"items": rows})
