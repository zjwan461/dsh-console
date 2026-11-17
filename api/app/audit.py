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
    *,
    ip_override: str | None = None,
    ua_override: str | None = None,
    request_id: str | None = None,
) -> AuditLog:
    """Record an audit row.

    `ip_override` / `ua_override` exist for WebSocket handlers, where there is no
    `Request` but the client address and user agent are still available.
    """
    entry = AuditLog(
        actor_id=actor.id if actor else None,
        actor_name=actor.username if actor else "system",
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        ip=ip_override or (client_ip(request) if request else None),
        user_agent=ua_override or (user_agent(request) if request else None),
        detail=detail,
        request_id=request_id or (getattr(request.state, "request_id", None) if request else None),
    )
    db.add(entry)
    return entry
