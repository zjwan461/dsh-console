from __future__ import annotations

from fastapi import Depends, Request, Response
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .errors import ApiError
from .models import Role, User, UserSession, UserStatus
from .security import utcnow

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def user_agent(request: Request) -> str:
    return request.headers.get("user-agent", "")[:255]


def load_session(request: Request, db: Session) -> UserSession | None:
    sid = request.cookies.get(settings.cookie_name)
    if not sid:
        return None
    session = db.get(UserSession, sid)
    if session is None or session.revoked_at is not None or session.expires_at <= utcnow():
        return None
    return session


def get_session(request: Request, db: Session = Depends(get_db)) -> UserSession:
    session = load_session(request, db)
    if session is None:
        raise ApiError("AUTH_SESSION_EXPIRED", "登录已过期，请重新登录", 401)
    request.state.session = session
    return session


def current_user(
    request: Request,
    session: UserSession = Depends(get_session),
    db: Session = Depends(get_db),
) -> User:
    user = db.get(User, session.user_id)
    if user is None or user.deleted_at is not None:
        raise ApiError("AUTH_SESSION_EXPIRED", "账号不存在", 401)
    if user.status != UserStatus.active:
        raise ApiError("AUTH_ACCOUNT_DISABLED", "账号已被禁用", 403)
    request.state.user = user
    # Slide last_seen at most once a minute to avoid a write per request.
    if (utcnow() - session.last_seen_at).total_seconds() > 60:
        session.last_seen_at = utcnow()
        db.commit()
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != Role.admin:
        raise ApiError("FORBIDDEN", "需要管理员权限", 403)
    return user


def csrf_guard(request: Request, db: Session = Depends(get_db)) -> None:
    """Router-level dependency: enforce the double-submit token on writes."""
    if request.method in SAFE_METHODS:
        return
    session = load_session(request, db)
    if session is None:
        raise ApiError("AUTH_SESSION_EXPIRED", "登录已过期，请重新登录", 401)
    provided = request.headers.get("x-csrf-token", "")
    if not provided or provided != session.csrf_token:
        raise ApiError("AUTH_CSRF_INVALID", "CSRF 校验失败，请刷新页面后重试", 403)


def set_session_cookie(response: Response, session_id: str, max_age: int | None) -> None:
    response.set_cookie(
        key=settings.cookie_name,
        value=session_id,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.cookie_name,
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
