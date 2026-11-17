from __future__ import annotations

import shutil

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import settings
from ..drivers import DriverError, get_driver
from ..errors import ApiError
from ..models import Instance, InstanceStatus


def probe_capacity(db: Session) -> dict:
    """Current host capacity.

    Never raises: an unreachable Docker daemon must degrade the dashboards, not
    turn them into 500s. Callers that need to *act* on capacity use `admit()`,
    which refuses when the probe could not reach the runtime.
    """
    try:
        info = get_driver().info()
        docker_ok = True
        docker_error = None
    except DriverError as exc:
        info = None
        docker_ok = False
        docker_error = str(exc)[:200]

    mem_total_mb = int(info.mem_total_bytes / (1024 * 1024)) if info else 0
    reserve_mb = max(
        settings.memory_reserve_mb, int(mem_total_mb * settings.memory_reserve_percent / 100)
    )

    rows = (
        db.query(
            func.coalesce(func.sum(Instance.memory_mb), 0),
            func.coalesce(func.sum(Instance.cpu_cores), 0),
            func.coalesce(func.sum(Instance.disk_gb), 0),
        )
        .filter(Instance.status != InstanceStatus.deleted)
        .one()
    )
    mem_committed_mb, cpu_committed, disk_committed = int(rows[0]), float(rows[1]), int(rows[2])

    try:
        usage = shutil.disk_usage(settings.data_root)
        disk_total_gb = int(usage.total / (1024**3))
        disk_free_gb = int(usage.free / (1024**3))
    except OSError:
        disk_total_gb = 0
        disk_free_gb = 0

    mem_limit_mb = max(0, mem_total_mb - reserve_mb)
    cpu_cores = info.cpu_cores if info else 0
    cpu_limit = cpu_cores * settings.cpu_overcommit
    disk_limit_gb = int(disk_total_gb * settings.disk_use_ratio)

    return {
        "docker_ok": docker_ok,
        "docker_error": docker_error,
        "cpu_cores": cpu_cores,
        "cpu_committed": round(cpu_committed, 2),
        "cpu_limit": round(cpu_limit, 2),
        "cpu_overcommit": settings.cpu_overcommit,
        "mem_total_mb": mem_total_mb,
        "mem_committed_mb": mem_committed_mb,
        "mem_reserve_mb": reserve_mb,
        "mem_limit_mb": mem_limit_mb,
        "disk_total_gb": disk_total_gb,
        "disk_free_gb": disk_free_gb,
        "disk_committed_gb": disk_committed,
        "disk_limit_gb": disk_limit_gb,
        "containers_running": info.containers_running if info else 0,
        "docker_version": info.docker_version if info else "unavailable",
        "can_admit": {
            "cpu": docker_ok and cpu_committed < cpu_limit,
            "memory": docker_ok and mem_committed_mb < mem_limit_mb,
            "disk": docker_ok and disk_committed < disk_limit_gb,
        },
    }


def admit(capacity: dict, cpu_cores: float, memory_mb: int, disk_gb: int) -> None:
    if not capacity.get("docker_ok", True):
        raise ApiError(
            "DOCKER_UNAVAILABLE",
            "容器运行时不可用，暂时无法创建实例",
            503,
            {"error": capacity.get("docker_error")},
        )
    if capacity["mem_committed_mb"] + memory_mb > capacity["mem_limit_mb"]:
        raise ApiError(
            "INSTANCE_QUOTA_MEMORY",
            "宿主内存余量不足，请联系管理员",
            409,
            {
                "total_mb": capacity["mem_total_mb"],
                "committed_mb": capacity["mem_committed_mb"],
                "reserve_mb": capacity["mem_reserve_mb"],
                "required_mb": memory_mb,
            },
        )
    if capacity["cpu_committed"] + cpu_cores > capacity["cpu_limit"]:
        raise ApiError(
            "INSTANCE_QUOTA_CPU",
            "宿主 CPU 余量不足，请联系管理员",
            409,
            {"limit": capacity["cpu_limit"], "committed": capacity["cpu_committed"]},
        )
    if (
        capacity["disk_limit_gb"]
        and capacity["disk_committed_gb"] + disk_gb > capacity["disk_limit_gb"]
    ):
        raise ApiError(
            "INSTANCE_QUOTA_DISK",
            "数据盘余量不足，请联系管理员",
            409,
            {"limit_gb": capacity["disk_limit_gb"], "committed_gb": capacity["disk_committed_gb"]},
        )
