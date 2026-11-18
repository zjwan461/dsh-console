from __future__ import annotations

import html
import json
import logging
import re
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..audit import write_audit
from ..config import settings
from ..db import get_db
from ..deps import csrf_guard, current_user
from ..drivers import DriverError, get_driver
from ..errors import ApiError, envelope
from ..models import DshImage, Instance, InstanceEvent, InstanceStatus, Plan, User
from ..redis import get_redis
from ..schemas import InstanceCreateIn, InstancePatchIn
from ..security import new_token
from ..services import caddy
from ..services.capacity import probe_capacity
from ..services.instances import (
    _try_lock,
    acquire_lock,
    available_images,
    create_instance,
    default_image,
    delete_instance,
    get_instance,
    instance_view,
    provision,
    public_authority,
    resolve_token,
    restart_instance,
    start_instance,
    stop_instance,
)
from ..services.quota import effective, ensure_quota
from ..services.settings import get_setting

log = logging.getLogger("dsh.api.instances")

router = APIRouter(prefix="/instances", tags=["instances"], dependencies=[Depends(csrf_guard)])
public_router = APIRouter(tags=["instances"])


def _schedule(background: BackgroundTasks, fn, *args, **kwargs) -> None:
    if settings.jobs_inline:
        fn(*args, **kwargs)
    else:
        background.add_task(fn, *args, **kwargs)


def _require_instance(db: Session, user: User) -> Instance:
    instance = get_instance(db, user.id)
    if instance is None:
        raise ApiError("INSTANCE_NOT_FOUND", "还没有实例，请先创建", 404)
    return instance


@router.get("/me")
def my_instance(
    request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    instance = get_instance(db, user.id)
    return envelope(request, instance_view(instance, db) if instance else None)


@router.get("/me/options")
def options(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    quota = ensure_quota(db, user)
    plan = db.get(Plan, quota.plan_id) if quota.plan_id else user.plan
    images = available_images(db)
    return envelope(
        request,
        {
            "plans": [
                {
                    "id": p.id,
                    "code": p.code,
                    "name": p.name,
                    "cpu_cores": float(p.cpu_cores),
                    "memory_mb": p.memory_mb,
                    "disk_gb": p.disk_gb,
                    "monthly_minutes": p.monthly_minutes,
                    "is_default": p.is_default,
                }
                for p in db.query(Plan)
                .filter(Plan.enabled.is_(True))
                .order_by(Plan.sort_order)
                .all()
            ],
            "images": [
                {
                    "id": img.id,
                    "version": img.version,
                    "size_bytes": img.size_bytes,
                    "is_default": img.is_default,
                    "changelog": img.changelog,
                }
                for img in images
            ],
            "default_image_id": (default_image(db).id if default_image(db) else None),
            "quota": effective(quota, plan),
            "capacity": probe_capacity(db),
            "can_create": get_instance(db, user.id) is None and bool(images),
        },
    )


@router.post("/me", status_code=202)
def create(
    payload: InstanceCreateIn,
    request: Request,
    background: BackgroundTasks,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    image = db.get(DshImage, payload.image_id) if payload.image_id else default_image(db)
    if image is None:
        raise ApiError("IMAGE_DISABLED", "还没有可用版本，请联系管理员导入", 409)
    instance = create_instance(
        db,
        user,
        image=image,
        idle_timeout_seconds=payload.idle_timeout_seconds,
        actor_id=user.id,
    )
    write_audit(
        db,
        request,
        user,
        "instance.create",
        "instance",
        instance.id,
        {"image_id": image.id, "version": image.version},
    )
    db.commit()
    acquire_lock(instance.id, instance.status.value)
    _schedule(background, provision, instance.id, prelocked=True)
    db.refresh(instance)
    return envelope(
        request,
        {"id": instance.id, "status": instance.status.value, "job": "provision"},
    )


@router.post("/me/start", status_code=202)
def start(
    request: Request,
    background: BackgroundTasks,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    instance = _require_instance(db, user)
    if instance.status in {
        InstanceStatus.running,
        InstanceStatus.starting,
        InstanceStatus.provisioning,
    }:
        return envelope(request, {"status": instance.status.value})
    if instance.status == InstanceStatus.deleting:
        raise ApiError("INSTANCE_STATE_INVALID", "实例正在删除", 409)
    write_audit(db, request, user, "instance.start", "instance", instance.id)
    db.commit()
    acquire_lock(instance.id, instance.status.value)
    _schedule(background, start_instance, instance.id, user.id, prelocked=True)
    return envelope(request, {"status": "starting"})


@router.post("/me/stop", status_code=202)
def stop(
    request: Request,
    background: BackgroundTasks,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    instance = _require_instance(db, user)
    if instance.status in {InstanceStatus.stopped, InstanceStatus.sleeping}:
        return envelope(request, {"status": instance.status.value})
    write_audit(db, request, user, "instance.stop", "instance", instance.id)
    db.commit()
    acquire_lock(instance.id, instance.status.value)
    _schedule(background, stop_instance, instance.id, sleep=False, actor_id=user.id, prelocked=True)
    return envelope(request, {"status": "stopping"})


@router.post("/me/sleep", status_code=202)
def sleep(
    request: Request,
    background: BackgroundTasks,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    instance = _require_instance(db, user)
    acquire_lock(instance.id, instance.status.value)
    _schedule(background, stop_instance, instance.id, sleep=True, actor_id=user.id, prelocked=True)
    return envelope(request, {"status": "sleeping"})


@router.post("/me/restart", status_code=202)
def restart(
    request: Request,
    background: BackgroundTasks,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    instance = _require_instance(db, user)
    if instance.status == InstanceStatus.deleting:
        raise ApiError("INSTANCE_STATE_INVALID", "实例正在删除", 409)
    acquire_lock(instance.id, instance.status.value)
    _schedule(background, restart_instance, instance.id, user.id, prelocked=True)
    return envelope(request, {"status": "starting"})


@router.patch("/me")
def patch(
    payload: InstancePatchIn,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    instance = _require_instance(db, user)
    if payload.idle_timeout_seconds is not None:
        instance.idle_timeout_seconds = payload.idle_timeout_seconds
    db.commit()
    return envelope(request, instance_view(instance, db))


@router.delete("/me", status_code=204)
def delete(
    request: Request,
    confirm: str = Query(min_length=1),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    instance = _require_instance(db, user)
    if confirm != user.username:
        raise ApiError("VALIDATION_ERROR", "请输入用户名确认删除", 400)
    write_audit(db, request, user, "instance.delete", "instance", instance.id)
    delete_instance(db, instance, user.id)
    return None


@router.get("/me/logs")
def logs(
    request: Request,
    tail: int = Query(default=200, ge=1, le=1000),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    instance = _require_instance(db, user)
    if not instance.container_id:
        return envelope(request, {"lines": [], "truncated": False})
    try:
        text = get_driver().logs(instance.container_id, tail=tail)
    except DriverError as exc:
        raise ApiError("DOCKER_UNAVAILABLE", str(exc), 503) from exc
    # The launch token lives in these logs; never hand it back to the browser.
    text = re.sub(r"token=[A-Za-z0-9_-]+", "token=***", text)
    lines = text.splitlines()
    return envelope(request, {"lines": lines[-tail:], "truncated": len(lines) > tail})


@router.get("/me/stats")
def stats(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    instance = _require_instance(db, user)
    if instance.status != InstanceStatus.running or not instance.container_id:
        raise ApiError("INSTANCE_NOT_RUNNING", "实例未运行", 409)
    try:
        data = get_driver().stats(instance.container_id)
    except DriverError as exc:
        raise ApiError("DOCKER_UNAVAILABLE", str(exc), 503) from exc
    data["read_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return envelope(request, data)


@router.get("/me/events")
def events(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    instance = _require_instance(db, user)
    rows = (
        db.query(InstanceEvent)
        .filter(InstanceEvent.instance_id == instance.id)
        .order_by(InstanceEvent.id.desc())
        .limit(limit)
        .all()
    )
    return envelope(
        request,
        {
            "items": [
                {
                    "id": row.id,
                    "type": row.type,
                    "from": row.from_status,
                    "to": row.to_status,
                    "message": row.message,
                    "created_at": row.created_at.isoformat() + "Z",
                }
                for row in rows
            ]
        },
    )


@router.post("/me/launch")
def launch(
    request: Request,
    background: BackgroundTasks,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    instance = _require_instance(db, user)
    if instance.status in {InstanceStatus.stopped, InstanceStatus.sleeping, InstanceStatus.error}:
        # Launch is a convenience path: if another operation holds the lock the
        # instance is already coming up, so report progress instead of 409.
        if _try_lock(instance.id):
            _schedule(background, start_instance, instance.id, user.id, prelocked=True)
        return envelope(request, {"state": "starting", "retry_after_ms": 1500})
    if instance.status != InstanceStatus.running:
        return envelope(request, {"state": instance.status.value, "retry_after_ms": 1500})
    token = resolve_token(instance)
    if not token:
        raise ApiError(
            "TOKEN_UNAVAILABLE",
            "无法获取登录凭据，请重启实例后重试",
            410,
        )
    ticket = new_token(24)
    get_redis().set(
        f"ticket:{ticket}",
        json.dumps({"user_id": user.id, "instance_id": instance.id}),
        ex=60,
    )
    domain = get_setting(db, "base_domain", settings.base_domain)
    return envelope(
        request,
        {
            "state": "running",
            "url": (
                f"{settings.public_scheme}://console.{domain}"
                f"{public_authority(domain)}/go/{ticket}"
            ),
        },
    )


@public_router.get("/go/{ticket}")
def go(ticket: str):
    redis = get_redis()
    raw = redis.getdel(f"ticket:{ticket}")
    if not raw:
        return HTMLResponse("<h1>链接已失效</h1><p>请回到控制台重新打开。</p>", status_code=410)
    data = json.loads(raw)
    from ..db import SessionLocal

    db = SessionLocal()
    try:
        instance = db.get(Instance, int(data["instance_id"]))
        token = resolve_token(instance) if instance else None
        if instance is None or not token:
            return HTMLResponse(
                "<h1>无法获取登录凭据</h1><p>请回控制台重启实例。</p>", status_code=410
            )
        domain = get_setting(db, "base_domain", settings.base_domain)
        url = f"{settings.public_scheme}://{instance.subdomain}.{domain}/?token={token}"
        response = RedirectResponse(url, status_code=302)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response
    finally:
        db.close()


@public_router.get("/w/{wake_key}", response_class=HTMLResponse)
def wake(wake_key: str, background: BackgroundTasks):
    from ..db import SessionLocal

    db = SessionLocal()
    try:
        instance = (
            db.query(Instance)
            .filter(Instance.wake_key == wake_key, Instance.status != InstanceStatus.deleted)
            .first()
        )
        if instance is None:
            return HTMLResponse("<h1>实例不存在</h1>", status_code=404)
        if instance.status not in {
            InstanceStatus.running,
            InstanceStatus.starting,
            InstanceStatus.waking,
        }:
            if _try_lock(instance.id):
                _schedule(background, start_instance, instance.id, prelocked=True)
        domain = html.escape(str(get_setting(db, "base_domain", settings.base_domain)))
        name = html.escape(instance.subdomain)
        page = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>__NAME__ · 正在唤醒</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{font-family:system-ui,sans-serif;background:#0f172a;color:#e2e8f0;
display:grid;place-items:center;height:100vh;margin:0}
.box{text-align:center}
.dot{width:42px;height:42px;border-radius:50%;border:3px solid #334155;
border-top-color:#4d6bfe;animation:s 1s linear infinite;margin:0 auto 18px}
@keyframes s{to{transform:rotate(360deg)}}
a{color:#93b4ff}
</style></head>
<body><div class="box"><div class="dot"></div><h2>正在唤醒 __NAME__</h2>
<p>首次启动可能需要 30 秒，请稍候…</p>
<p><a href="https://console.__DOMAIN__">回控制台</a></p>
</div><script>
const key = __KEY__;
async function poll() {
  try {
    const r = await fetch('/w/' + key + '/status', {headers: {'Accept': 'application/json'}});
    const j = await r.json();
    if (j.data && j.data.state === 'running') { location.replace(j.data.redirect); return; }
  } catch (e) {}
  setTimeout(poll, 2000);
}
setTimeout(poll, 1500);
</script></body></html>"""
        page = (
            page.replace("__NAME__", name)
            .replace("__DOMAIN__", domain)
            .replace("__KEY__", json.dumps(wake_key))
        )
        return HTMLResponse(page, status_code=200)
    finally:
        db.close()


@public_router.get("/w/{wake_key}/status")
def wake_status(wake_key: str):
    from ..db import SessionLocal

    db = SessionLocal()
    try:
        instance = (
            db.query(Instance)
            .filter(Instance.wake_key == wake_key, Instance.status != InstanceStatus.deleted)
            .first()
        )
        if instance is None:
            return {"data": {"state": "missing"}}
        state = instance.status.value
        domain = get_setting(db, "base_domain", settings.base_domain)
        redirect = f"{settings.public_scheme}://{instance.subdomain}.{domain}/"
        return {"data": {"state": state, "redirect": redirect if state == "running" else None}}
    finally:
        db.close()


def apply_routes(db: Session) -> None:
    try:
        caddy.apply(db)
    except caddy.CaddyError as exc:
        log.warning("caddy apply failed: %s", exc)
