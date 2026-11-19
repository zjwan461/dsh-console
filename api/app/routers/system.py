from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..deps import csrf_guard, current_user, require_admin
from ..drivers import get_driver
from ..errors import envelope
from ..models import Role, User
from ..redis import redis_ok
from ..security import utcnow
from ..services.settings import get_setting
from ..version import __version__

router = APIRouter(prefix="/system", tags=["system"], dependencies=[Depends(csrf_guard)])


@router.get("/info")
def info(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    terminal_enabled = False
    if user.role == Role.admin:
        terminal_enabled = bool(get_setting(db, "admin_terminal_enabled", True))
    return envelope(
        request,
        {
            "name": settings.console_name,
            "base_domain": settings.base_domain,
            "console_url": f"https://console.{settings.base_domain}",
            "version": "0.1.0",
            "env": settings.env,
            "server_time": utcnow().isoformat() + "Z",
            # Lets the admin UI grey out entry points the server would reject.
            "features": {"terminal": terminal_enabled},
        },
    )


@router.get("/health")
def health(request: Request, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    db_ok = True
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_ok = False

    docker = {"ok": False}
    try:
        info = get_driver().info()
        docker = {
            "ok": True,
            "version": info.docker_version,
            "containers": info.containers_running,
            "cpu_cores": info.cpu_cores,
        }
    except Exception as exc:
        docker = {"ok": False, "error": str(exc)[:200]}

    caddy = {"ok": False}
    if settings.caddy_enabled:
        try:
            import httpx

            response = httpx.get(f"{settings.caddy_admin_url.rstrip('/')}/config/", timeout=2)
            caddy = {"ok": response.status_code < 400, "status": response.status_code}
        except Exception as exc:
            caddy = {"ok": False, "error": str(exc)[:200]}
    else:
        caddy = {"ok": False, "note": "CADDY_ENABLED=0"}

    capacity = None
    try:
        from ..services.capacity import probe_capacity

        capacity = probe_capacity(db)
    except Exception:
        capacity = None

    return envelope(
        request,
        {
            "mysql": {"ok": db_ok},
            "redis": {"ok": redis_ok()},
            "docker": docker,
            "caddy": caddy,
            "capacity": capacity,
            "version": __version__,
        },
    )


@router.post("/reconcile")
def reconcile(
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from ..services.reconcile import reconcile as run_reconcile

    return envelope(request, run_reconcile(db))
