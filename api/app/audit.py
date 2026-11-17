from __future__ import annotations

from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from .deps import client_ip, user_agent
from .models import AuditLog, User


def write_audit(
    db: Session,
    request: Request | None,
    actor: User | None,
    action: str,
    target_type: str = "",
    target_id: str | int = "",
    detail: dict[str, Any] | None = None,
) -> AuditLog:
    entry = AuditLog(
        actor_id=actor.id if actor else None,
        actor_name=actor.username if actor else "system",
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        ip=client_ip(request) if request else None,
        user_agent=user_agent(request) if request else None,
        detail=detail,
        request_id=getattr(request.state, "request_id", None) if request else None,
    )
    db.add(entry)
    return entry
