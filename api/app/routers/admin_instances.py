from __future__ import annotations

import asyncio
import json
import re
import time

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
    acquire_lock,
    delete_instance,
    instance_view,
    restart_instance,
    start_instance,
    stop_instance,
)
from ..services.settings import get_setting

router = APIRouter(
    prefix="/admin/instances", tags=["admin:instances"], dependencies=[Depends(csrf_guard)]
)
# WebSocket routes cannot carry the HTTP CSRF dependency: FastAPI would pass a
# WebSocket where csrf_guard expects a Request, which fails the handshake with a
# 500. The terminal endpoint authenticates by cookie instead.
ws_router = APIRouter(prefix="/admin/instances", tags=["admin:instances"])


class _IdleTimeout(Exception):
    """No input for longer than TERMINAL_IDLE_SECONDS."""


async def _safe_receive(ws: WebSocket, timeout: float | None = None) -> dict | None:
    """Receive one message, returning None once the peer is gone.

    Starlette raises WebSocketDisconnect on the first post-close receive and a
    bare RuntimeError ("Cannot call receive once a disconnect message has been
    received") on any later one, so both must be handled. With `timeout`, an
    idle session raises `_IdleTimeout` so the caller can close it politely.
    """
    try:
        if timeout:
            message = await asyncio.wait_for(ws.receive(), timeout)
        else:
            message = await ws.receive()
    except TimeoutError:
        raise _IdleTimeout from None
    except WebSocketDisconnect:
        return None
    except RuntimeError:
        return None
    if message.get("type") == "websocket.disconnect":
        return None
    return message


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
    acquire_lock(instance.id, instance.status.value)
    _schedule(background, start_instance, instance.id, admin.id, prelocked=True)
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
    acquire_lock(instance.id, instance.status.value)
    _schedule(
        background, stop_instance, instance.id, sleep=False, actor_id=admin.id, prelocked=True
    )
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
    acquire_lock(instance.id, instance.status.value)
    _schedule(background, restart_instance, instance.id, admin.id, prelocked=True)
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


@ws_router.websocket("/{instance_id}/terminal")
async def terminal(ws: WebSocket, instance_id: int):
    """Interactive admin shell into a user's container.

    Wire protocol
    -------------
    client -> server : binary frames are keystrokes (raw bytes, written straight
                       to the PTY); text frames are JSON control messages:
                       {"type":"resize","cols":120,"rows":32}, {"type":"ping"}
    server -> client : binary frames are PTY output (xterm.js writes them as-is);
                       text frames are JSON control messages:
                       {"type":"ready","shell":"bash"}, {"type":"error",...}

    The PTY is a real `docker exec` with `tty=True`, so job control, colours,
    tab completion and full-screen programs all behave like a local shell.
    """
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
    client_host = ws.client.host if ws.client else None
    client_ua = ws.headers.get("user-agent")

    def audit(action: str, detail: dict) -> None:
        """WebSocket handlers have no Request, so record the peer explicitly."""
        with SessionLocal() as db:
            actor = db.get(User, user_id)
            write_audit(
                db,
                None,
                actor,
                action,
                "instance",
                instance_id,
                detail,
                ip_override=client_host,
                ua_override=client_ua,
            )
            db.commit()

    with SessionLocal() as db:
        instance = db.get(Instance, instance_id)
        container_id = instance.container_id if instance else None
        terminal_enabled = bool(get_setting(db, "admin_terminal_enabled", True))
    if not terminal_enabled:
        audit("admin.terminal_denied", {"reason": "disabled by setting"})
        await ws.send_text(json.dumps({"type": "error", "message": "管理员已关闭 Web 终端"}))
        await ws.close(code=1008)
        return
    if not container_id:
        await ws.send_text(json.dumps({"type": "error", "message": "实例没有运行中的容器"}))
        await ws.close()
        return

    driver = get_driver()
    try:
        exec_id, raw = driver.open_exec(container_id, ["/bin/bash", "-l"], tty=True)
    except DriverError as exc:
        await ws.send_text(json.dumps({"type": "error", "message": str(exc)}))
        await ws.close()
        return

    started = time.monotonic()
    bytes_in = 0
    bytes_out = 0
    close_reason = "client_closed"
    audit("admin.terminal_open", {"container": container_id[:12], "exec": exec_id[:12]})
    await ws.send_text(json.dumps({"type": "ready", "shell": "bash"}))

    async def pump_output() -> None:
        nonlocal bytes_out
        while True:
            data = await asyncio.to_thread(raw.recv, 65536)
            if not data:
                break
            bytes_out += len(data)
            await ws.send_bytes(data)

    async def pump_input() -> None:
        nonlocal bytes_in, close_reason
        idle = settings.terminal_idle_seconds or None
        while True:
            try:
                message = await _safe_receive(ws, timeout=idle)
            except _IdleTimeout:
                close_reason = "idle_timeout"
                await ws.send_text(
                    json.dumps(
                        {
                            "type": "error",
                            "message": f"空闲超过 {idle // 60} 分钟，已自动断开",
                        }
                    )
                )
                return
            if message is None:
                return
            payload = message.get("bytes")
            if payload is not None:
                bytes_in += len(payload)
                await asyncio.to_thread(raw.sendall, payload)
                continue
            text = message.get("text")
            if text is None:
                continue
            control = _parse_control(text)
            if control is not None:
                if control.get("type") == "resize":
                    try:
                        cols = max(2, min(500, int(control.get("cols", 80))))
                        rows = max(2, min(300, int(control.get("rows", 24))))
                    except (TypeError, ValueError):
                        continue
                    await asyncio.to_thread(driver.resize_exec, exec_id, rows, cols)
                elif control.get("type") == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))
                continue
            # Not JSON: treat it as typed input (older clients / manual testing).
            bytes_in += len(text.encode())
            await asyncio.to_thread(raw.sendall, text.encode())

    output_task = asyncio.create_task(pump_output())
    input_task = asyncio.create_task(pump_input())
    try:
        await asyncio.wait({output_task, input_task}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (output_task, input_task):
            task.cancel()
        try:
            raw.close()
        except Exception:  # noqa: BLE001 - closing a dead PTY is best effort
            pass
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
        audit(
            "admin.terminal_close",
            {
                "container": container_id[:12],
                "reason": close_reason,
                "seconds": round(time.monotonic() - started, 1),
                "bytes_in": bytes_in,
                "bytes_out": bytes_out,
            },
        )


def _parse_control(text: str) -> dict | None:
    """Return the parsed JSON object when `text` is a control message."""
    stripped = text.strip()
    if not stripped.startswith("{"):
        return None
    try:
        payload = json.loads(stripped)
    except ValueError:
        return None
    if isinstance(payload, dict) and "type" in payload:
        return payload
    return None
