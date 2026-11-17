from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_admin
from ..errors import envelope
from ..models import AuditLog, User
from ..pagination import Page, paginated

router = APIRouter(prefix="/admin/audit", tags=["admin:audit"])


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


@router.get("")
def list_audit(
    request: Request,
    actor_id: int | None = Query(default=None),
    action: str | None = Query(default=None, max_length=64),
    since: str | None = Query(default=None, alias="from"),
    until: str | None = Query(default=None, alias="to"),
    page: Page = Depends(),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = db.query(AuditLog)
    if actor_id:
        query = query.filter(AuditLog.actor_id == actor_id)
    if action:
        query = query.filter(AuditLog.action.like(f"{action}%"))
    start, end = _dt(since), _dt(until)
    if start:
        query = query.filter(AuditLog.created_at >= start)
    if end:
        query = query.filter(AuditLog.created_at <= end)
    total = query.count()
    rows = query.order_by(AuditLog.id.desc()).offset(page.offset).limit(page.page_size).all()
    items = [
        {
            "id": row.id,
            "actor_id": row.actor_id,
            "actor_name": row.actor_name,
            "action": row.action,
            "target_type": row.target_type,
            "target_id": row.target_id,
            "ip": row.ip,
            "user_agent": row.user_agent,
            "detail": row.detail,
            "request_id": row.request_id,
            "created_at": row.created_at.isoformat() + "Z",
        }
        for row in rows
    ]
    return envelope(request, paginated(items, total, page))
