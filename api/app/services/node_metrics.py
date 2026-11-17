from __future__ import annotations

import os
import shutil
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..config import settings
from ..drivers import get_driver
from ..models import NodeMetric


def collect(db: Session) -> NodeMetric | None:
    if not settings.node_metrics_enabled:
        return None
    try:
        import psutil
    except ImportError:  # pragma: no cover
        return None

    cpu = psutil.cpu_percent(interval=None)
    memory = psutil.virtual_memory()
    try:
        load1, load5, load15 = os.getloadavg()
    except (AttributeError, OSError):
        load1 = load5 = load15 = 0.0
    net = psutil.net_io_counters()
    disks = []
    for mount in {"/", settings.data_root}:
        if not os.path.exists(mount):
            continue
        try:
            usage = shutil.disk_usage(mount)
        except OSError:
            continue
        disks.append(
            {
                "mount": mount,
                "total": usage.total,
                "used": usage.used,
                "free": usage.free,
            }
        )
    try:
        containers = get_driver().info().containers_running
    except Exception:
        containers = 0

    metric = NodeMetric(
        ts=datetime.utcnow(),
        cpu_percent=round(cpu, 2),
        load1=round(load1, 2),
        load5=round(load5, 2),
        load15=round(load15, 2),
        mem_total_bytes=int(memory.total),
        mem_used_bytes=int(memory.used),
        swap_used_bytes=int(getattr(memory, "swap_used", 0) or 0),
        net_rx_bytes=int(net.bytes_recv),
        net_tx_bytes=int(net.bytes_sent),
        containers_running=containers,
        disks=disks,
    )
    db.add(metric)
    db.commit()
    return metric


def prune(db: Session) -> int:
    cutoff = datetime.utcnow() - timedelta(days=settings.node_metrics_retention_days)
    deleted = db.query(NodeMetric).filter(NodeMetric.ts < cutoff).delete()
    db.commit()
    return int(deleted or 0)


def series(db: Session, start: datetime, end: datetime, step: int = 30) -> list[dict]:
    rows = (
        db.query(NodeMetric)
        .filter(NodeMetric.ts >= start, NodeMetric.ts < end)
        .order_by(NodeMetric.ts)
        .all()
    )
    if step <= 30 or len(rows) <= 1:
        selected = rows
    else:
        selected = []
        last_ts = None
        for row in rows:
            if last_ts is None or (row.ts - last_ts).total_seconds() >= step:
                selected.append(row)
                last_ts = row.ts
    return [
        {
            "ts": row.ts.isoformat() + "Z",
            "cpu_percent": float(row.cpu_percent),
            "load1": float(row.load1),
            "load5": float(row.load5),
            "load15": float(row.load15),
            "mem_total_bytes": row.mem_total_bytes,
            "mem_used_bytes": row.mem_used_bytes,
            "net_rx_bytes": row.net_rx_bytes,
            "net_tx_bytes": row.net_tx_bytes,
            "containers_running": row.containers_running,
            "disks": row.disks or [],
        }
        for row in selected
    ]
