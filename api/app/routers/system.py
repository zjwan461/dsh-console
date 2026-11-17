from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..deps import current_user, require_admin
from ..errors import envelope
from ..models import User
from ..redis import redis_ok
from ..security import utcnow

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/info")
def info(request: Request, user: User = Depends(current_user)):
    return envelope(
        request,
        {
            "name": settings.console_name,
            "base_domain": settings.base_domain,
            "console_url": f"https://console.{settings.base_domain}",
            "version": "0.1.0",
            "env": settings.env,
            "server_time": utcnow().isoformat() + "Z",
        },
    )


@router.get("/health")
def health(request: Request, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    db_ok = True
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    return envelope(
        request,
        {
            "mysql": {"ok": db_ok},
            "redis": {"ok": redis_ok()},
            "docker": {"ok": False, "note": "docker driver ships in M2"},
            "caddy": {"ok": False, "note": "caddy integration ships in M2"},
            "version": "0.1.0",
        },
    )
