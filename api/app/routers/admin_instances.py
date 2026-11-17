from __future__ import annotations

import asyncio
import re

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from sqlalchemy.orm import Session

from ..audit import write_audit
from ..config import settings
from ..db import SessionLocal, get_db
from ..deps import csrf_guard, require_admin
from ..drivers import DriverError, get_driver
from ..errors import ApiError, envelope
from ..models import DshImage, Instance, InstanceStatus, Role, User, UserSession
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


@router.websocket("/{instance_id}/terminal")
async def terminal(ws: WebSocket, instance_id: int):
    sid = ws.cookies.get(settings.cookie_name)
    user_id = None
    with SessionLocal() as db:
        session = db.get(UserSession, sid) if sid else None
        if session is not None and session.revoked_at is None:
            user = db.get(User, session.user_id)
            if user is not None and user.role == Role.admin and user.deleted_at is None:
                user_id = user.id
    if user_id is None:
        await ws.close(code=1008)
        return
    await ws.accept()

    with SessionLocal() as db:
        instance = db.get(Instance, instance_id)
        container_id = instance.container_id if instance else None
    if not container_id:
        await ws.send_text("实例没有运行中的容器\r\n")
        await ws.close()
        return

    driver = get_driver()
    if settings.docker_fake or not hasattr(driver, "client"):
        await ws.send_text("FakeDriver terminal: 输入任意命令会原样回显。\r\n")
        try:
            while True:
                message = await ws.receive()
                if message.get("text"):
                    await ws.send_text(f"$ {message['text']}")
        except WebSocketDisconnect:
            return

    try:
        container = driver.client.containers.get(container_id)
        result = container.exec_run(["/bin/bash", "-l"], tty=True, stdin=True, socket=True)
        raw = result.output._sock
    except Exception as exc:
        await ws.send_text(f"无法打开终端：{exc}\r\n")
        await ws.close()
        return

    async def pump_output() -> None:
        while True:
            data = await asyncio.to_thread(raw.recv, 4096)
            if not data:
                break
            await ws.send_bytes(data)

    async def pump_input() -> None:
        while True:
            message = await ws.receive()
            if message.get("bytes"):
                await asyncio.to_thread(raw.sendall, message["bytes"])
            elif message.get("text") is not None:
                await asyncio.to_thread(raw.sendall, message["text"].encode())

    output_task = asyncio.create_task(pump_output())
    input_task = asyncio.create_task(pump_input())
    try:
        done, pending = await asyncio.wait(
            {output_task, input_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
    finally:
        for task in (output_task, input_task):
            task.cancel()
        try:
            raw.close()
        except Exception:
            pass
        try:
            await ws.close()
        except Exception:
            pass
