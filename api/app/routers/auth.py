from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..audit import write_audit
from ..config import settings
from ..db import get_db
from ..deps import (
    clear_session_cookie,
    client_ip,
    csrf_guard,
    current_user,
    get_session,
    set_session_cookie,
    user_agent,
)
from ..errors import ApiError, envelope
from ..models import Invite, Plan, User, UserQuota, UserSession, UserStatus
from ..redis import rate_limit, reset_rate_limit
from ..schemas import LoginIn, PasswordChangeIn, RegisterIn, user_brief
from ..security import (
    hash_password,
    new_token,
    session_expiry,
    utcnow,
    validate_username,
    verify_password,
)
from ..services.quota import current_cycle, quota_view

router = APIRouter(prefix="/auth", tags=["auth"])


def _start_session(db: Session, request: Request, user: User) -> tuple[UserSession, str]:
    session = UserSession(
        id=new_token(),
        user_id=user.id,
        csrf_token=new_token(),
        ip=client_ip(request),
        user_agent=user_agent(request),
        expires_at=session_expiry(settings.session_ttl_days),
    )
    db.add(session)
    user.last_login_at = utcnow()
    user.last_login_ip = client_ip(request)
    return session, session.csrf_token


@router.post("/login")
def login(payload: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = client_ip(request)
    if not rate_limit(f"rl:login:ip:{ip}", settings.login_rate_per_minute, 60):
        raise ApiError("AUTH_RATE_LIMITED", "登录过于频繁，请稍后再试", 429)
    if not rate_limit(
        f"rl:login:user:{payload.username.lower()}", settings.login_rate_per_hour, 3600
    ):
        raise ApiError("AUTH_RATE_LIMITED", "该账号尝试次数过多，请 1 小时后再试", 429)

    user = db.query(User).filter(User.username == payload.username).first()
    if (
        user is None
        or user.deleted_at is not None
        or not verify_password(user.password_hash, payload.password)
    ):
        raise ApiError("AUTH_INVALID_CREDENTIALS", "账号或密码错误", 401)
    if user.status != UserStatus.active:
        raise ApiError("AUTH_ACCOUNT_DISABLED", "账号已被禁用，请联系管理员", 403)

    reset_rate_limit(f"rl:login:ip:{ip}")
    session, csrf = _start_session(db, request, user)
    write_audit(db, request, user, "auth.login", "user", user.id)
    db.commit()
    set_session_cookie(
        response,
        session.id,
        max_age=settings.session_ttl_days * 86400 if payload.remember else None,
    )
    return envelope(
        request,
        {
            "user": user_brief(user),
            "csrf_token": csrf,
            "expires_at": session.expires_at.isoformat() + "Z",
        },
    )


@router.post("/logout", status_code=204, dependencies=[Depends(csrf_guard)])
def logout(
    request: Request,
    response: Response,
    session: UserSession = Depends(get_session),
    db: Session = Depends(get_db),
):
    session.revoked_at = utcnow()
    db.commit()
    clear_session_cookie(response)


@router.get("/me")
def me(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    session = request.state.session
    return envelope(
        request,
        {
            "user": user_brief(user),
            "quota": quota_view(db, user),
            "csrf_token": session.csrf_token,
        },
    )


@router.get("/csrf")
def csrf(request: Request, session: UserSession = Depends(get_session)):
    return envelope(request, {"csrf_token": session.csrf_token})


@router.post("/password", status_code=204, dependencies=[Depends(csrf_guard)])
def change_password(
    payload: PasswordChangeIn,
    request: Request,
    user: User = Depends(current_user),
    session: UserSession = Depends(get_session),
    db: Session = Depends(get_db),
):
    if not verify_password(user.password_hash, payload.old_password):
        raise ApiError("AUTH_INVALID_CREDENTIALS", "原密码错误", 400)
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    db.query(UserSession).filter(
        UserSession.user_id == user.id, UserSession.id != session.id
    ).update({UserSession.revoked_at: utcnow()})
    write_audit(db, request, user, "auth.password_change", "user", user.id)
    db.commit()


@router.post("/invite/register", status_code=201)
def register(
    payload: RegisterIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    if not rate_limit(f"rl:register:ip:{client_ip(request)}", 3, 3600):
        raise ApiError("AUTH_RATE_LIMITED", "注册过于频繁，请稍后再试", 429)

    error = validate_username(payload.username)
    if error:
        raise ApiError("USERNAME_INVALID", error, 400)

    invite = db.query(Invite).filter(Invite.code == payload.code).with_for_update().first()
    if invite is None or invite.revoked_at is not None:
        raise ApiError("INVITE_INVALID", "邀请码无效", 400)
    if invite.expires_at is not None and invite.expires_at <= utcnow():
        raise ApiError("INVITE_INVALID", "邀请码已过期", 410)
    if invite.used_count >= invite.max_uses:
        raise ApiError("INVITE_EXHAUSTED", "邀请码已用尽", 410)

    if db.query(User.id).filter(User.username == payload.username).first():
        raise ApiError("USERNAME_TAKEN", "用户名已被占用", 409)
    if payload.email and db.query(User.id).filter(User.email == str(payload.email)).first():
        raise ApiError("EMAIL_TAKEN", "邮箱已被使用", 409)

    plan = db.query(Plan).filter(Plan.is_default.is_(True)).first()
    user = User(
        username=payload.username,
        subdomain=payload.username,
        display_name=payload.display_name or payload.username,
        email=str(payload.email) if payload.email else None,
        password_hash=hash_password(payload.password),
        role=invite.role,
        status=UserStatus.active,
        plan_id=plan.id if plan else None,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ApiError("USERNAME_TAKEN", "用户名已被占用", 409) from None

    start, end = current_cycle()
    db.add(
        UserQuota(
            user_id=user.id,
            plan_id=plan.id if plan else None,
            cycle_start=start,
            cycle_end=end,
        )
    )
    invite.used_count += 1
    session, csrf = _start_session(db, request, user)
    write_audit(db, request, user, "auth.register", "user", user.id, {"invite": invite.code})
    db.commit()
    set_session_cookie(response, session.id, max_age=settings.session_ttl_days * 86400)
    return envelope(
        request,
        {
            "user": user_brief(user),
            "csrf_token": csrf,
            "expires_at": session.expires_at.isoformat() + "Z",
        },
    )


@router.get("/sessions")
def list_sessions(
    request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    rows = (
        db.query(UserSession)
        .filter(
            UserSession.user_id == user.id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > utcnow(),
        )
        .order_by(UserSession.last_seen_at.desc())
        .all()
    )
    current = request.state.session.id
    return envelope(
        request,
        {
            "items": [
                {
                    "id": row.id,
                    "current": row.id == current,
                    "ip": row.ip,
                    "user_agent": row.user_agent,
                    "created_at": row.created_at.isoformat() + "Z",
                    "last_seen_at": row.last_seen_at.isoformat() + "Z",
                }
                for row in rows
            ]
        },
    )
