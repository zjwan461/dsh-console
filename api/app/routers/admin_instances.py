from __future__ import annotations

import re

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from sqlalchemy.orm import Session

from ..audit import write_audit
from ..config import settings
from ..db import get_db
from ..deps import csrf_guard, require_admin
from ..drivers import DriverError, get_driver
from ..errors import ApiError, envelope
from ..models import DshImage, Instance, InstanceStatus, User
from ..services.instances import (
    delete_instance,
    instance_view,
    restart_instance,
    start_instance,
    stop_instance,
)

router = APIRouter(
    prefix="/admin/instances", tags=["admin:instances"], dependencies=[Depends(csrf_guard)]
)


def _schedule(background: BackgroundTasks, fn, *args, **kwargs) -> None:
    if settings.jobs_inline:
        fn(*args, **kwargs)
    else:
        background.add_task(fn, *args, **kwargs)


def _row(db: Session, instance: Instance) -> dict:
    user = db.get(User, instance.user_id)
    data = instance_view(instance, db)
    data["user"] = {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "status": user.status.value,
    }
    data["container_id"] = instance.container_id
    return data


@router.get("")
def list_instances(
    request: Request,
    status: str | None = Query(default=None),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = db.query(Instance).filter(Instance.status != InstanceStatus.deleted)
    if status:
        query = query.filter(Instance.status == InstanceStatus(status))
    rows = query.order_by(Instance.id.desc()).limit(200).all()
    return envelope(request, {"items": [_row(db, i) for i in rows], "total": len(rows)})


@router.get("/{instance_id}")
def get_instance_detail(
    instance_id: int,
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    instance = db.get(Instance, instance_id)
    if instance is None:
        raise ApiError("INSTANCE_NOT_FOUND", "实例不存在", 404)
    data = _row(db, instance)
    image = db.get(DshImage, instance.image_id)
    data["image"] = (
        {"id": image.id, "version": image.version, "ref": image.image_ref} if image else None
    )
    return envelope(request, data)


@router.post("/{instance_id}/start", status_code=202)
def start(
    instance_id: int,
    request: Request,
    background: BackgroundTasks,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    instance = db.get(Instance, instance_id)
    if instance is None:
        raise ApiError("INSTANCE_NOT_FOUND", "实例不存在", 404)
    write_audit(db, request, admin, "admin.instance_start", "instance", instance.id)
    db.commit()
    _schedule(background, start_instance, instance.id, admin.id)
    return envelope(request, {"status": "starting"})


@router.post("/{instance_id}/stop", status_code=202)
def stop(
    instance_id: int,
    request: Request,
    background: BackgroundTasks,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    instance = db.get(Instance, instance_id)
    if instance is None:
        raise ApiError("INSTANCE_NOT_FOUND", "实例不存在", 404)
    write_audit(db, request, admin, "admin.instance_stop", "instance", instance.id)
    db.commit()
    _schedule(background, stop_instance, instance.id, sleep=False, actor_id=admin.id)
    return envelope(request, {"status": "stopping"})


@router.post("/{instance_id}/restart", status_code=202)
def restart(
    instance_id: int,
    request: Request,
    background: BackgroundTasks,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    instance = db.get(Instance, instance_id)
    if instance is None:
        raise ApiError("INSTANCE_NOT_FOUND", "实例不存在", 404)
    write_audit(db, request, admin, "admin.instance_restart", "instance", instance.id)
    db.commit()
    _schedule(background, restart_instance, instance.id, admin.id)
    return envelope(request, {"status": "starting"})


@router.delete("/{instance_id}", status_code=204)
def remove(
    instance_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    instance = db.get(Instance, instance_id)
    if instance is None:
        raise ApiError("INSTANCE_NOT_FOUND", "实例不存在", 404)
    write_audit(db, request, admin, "admin.instance_delete", "instance", instance.id)
    delete_instance(db, instance, admin.id)
    return None


@router.get("/{instance_id}/logs")
def logs(
    instance_id: int,
    request: Request,
    tail: int = Query(default=300, ge=1, le=2000),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    instance = db.get(Instance, instance_id)
    if instance is None or not instance.container_id:
        raise ApiError("INSTANCE_NOT_FOUND", "实例或容器不存在", 404)
    try:
        text = get_driver().logs(instance.container_id, tail=tail)
    except DriverError as exc:
        raise ApiError("DOCKER_UNAVAILABLE", str(exc), 503) from exc
    text = re.sub(r"token=[A-Za-z0-9_-]+", "token=***", text)
    return envelope(request, {"lines": text.splitlines()[-tail:]})
