from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..audit import write_audit
from ..config import settings
from ..db import get_db
from ..deps import csrf_guard, current_user
from ..errors import ApiError, envelope
from ..models import (
    Announcement,
    CredentialScope,
    Ticket,
    User,
)
from ..redis import get_redis
from ..schemas import CredentialIn, ProfileIn
from ..services import provider
from ..services.instances import get_instance, instance_view
from ..services.quota import quota_view
from ..services.settings import get_setting
from ..services.usage import user_usage

router = APIRouter(prefix="/me", tags=["me"], dependencies=[Depends(csrf_guard)])


def _range(start: str | None, end: str | None) -> tuple[datetime, datetime]:
    now = datetime.utcnow()
    if start:
        try:
            start_dt = datetime.fromisoformat(start.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            raise ApiError("VALIDATION_ERROR", "start 时间格式不正确", 422) from None
    else:
        start_dt = now - timedelta(hours=24)
    if end:
        try:
            end_dt = datetime.fromisoformat(end.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            raise ApiError("VALIDATION_ERROR", "end 时间格式不正确", 422) from None
    else:
        end_dt = now
    if end_dt <= start_dt:
        raise ApiError("VALIDATION_ERROR", "结束时间必须晚于开始时间", 422)
    if end_dt - start_dt > timedelta(days=90):
        raise ApiError("VALIDATION_ERROR", "查询范围不能超过 90 天", 422)
    return start_dt, end_dt


def _credential(db: Session, scope: CredentialScope, user_id: int):
    return provider.find(db, scope, user_id)


@router.get("/overview")
def overview(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    quota = quota_view(db, user)
    try:
        alert = get_redis().get(f"quota:alert:{user.id}")
    except Exception:
        alert = None
    if alert:
        quota["warning"] = alert
    instance = get_instance(db, user.id)
    now = datetime.utcnow()
    announcements = (
        db.query(Announcement)
        .filter(
            Announcement.status == "published",
            Announcement.publish_at.is_(None) | (Announcement.publish_at <= now),
            Announcement.expire_at.is_(None) | (Announcement.expire_at > now),
        )
        .order_by(Announcement.pinned.desc(), Announcement.id.desc())
        .limit(5)
        .all()
    )
    unread = (
        db.query(func.count(Ticket.id))
        .filter(Ticket.user_id == user.id, Ticket.status.in_(["open", "processing"]))
        .scalar()
        or 0
    )
    return envelope(
        request,
        {
            "user": {
                "id": user.id,
                "username": user.username,
                "display_name": user.display_name,
                "role": user.role.value,
                "subdomain": user.subdomain,
                "created_at": user.created_at.isoformat() + "Z",
            },
            "instance": instance_view(instance, db) if instance else None,
            "quota": quota,
            "base_domain": get_setting(db, "base_domain", settings.base_domain),
            "announcements": [
                {
                    "id": a.id,
                    "title": a.title,
                    "level": a.level,
                    "body_md": a.body_md,
                    "pinned": a.pinned,
                }
                for a in announcements
            ],
            "unread_tickets": unread,
            "features": {
                "self_key": True,
                "version_switch": True,
                "files": True,
                "snapshots": True,
            },
        },
    )


@router.get("/usage")
def usage(
    request: Request,
    start: str | None = Query(default=None, alias="from"),
    end: str | None = Query(default=None, alias="to"),
    granularity: str = Query(default="hour", pattern="^(hour|day)$"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    start_dt, end_dt = _range(start, end)
    data = user_usage(db, user.id, start_dt, end_dt, granularity)
    return envelope(request, data)


@router.patch("/profile")
def update_profile(
    payload: ProfileIn,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if payload.email is not None:
        exists = (
            db.query(User.id).filter(User.email == str(payload.email), User.id != user.id).first()
        )
        if exists:
            raise ApiError("EMAIL_TAKEN", "邮箱已被使用", 409)
        user.email = str(payload.email)
    if payload.display_name is not None:
        user.display_name = payload.display_name
    write_audit(db, request, user, "me.profile_update", "user", user.id)
    db.commit()
    return envelope(
        request,
        {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "email": user.email,
        },
    )


@router.get("/credentials")
def get_credentials(
    request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    platform = provider.find(db, CredentialScope.platform, 0)
    own = provider.find(db, CredentialScope.user, user.id)
    return envelope(
        request,
        {
            "platform_key_configured": platform is not None,
            "platform_base_url": platform.base_url if platform else None,
            "user_key": provider.view(own),
            "effective": "user" if own else ("platform" if platform else "none"),
            "effective_base_url": (own or platform).base_url if (own or platform) else None,
        },
    )


@router.put("/credentials")
def put_credentials(
    payload: CredentialIn,
    request: Request,
    response: Response,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    record, cleared = provider.save(
        db,
        scope=CredentialScope.user,
        user_id=user.id,
        api_key=payload.api_key,
        base_url=payload.base_url,
        actor_id=user.id,
        clear_key=payload.clear_key,
    )
    write_audit(
        db,
        request,
        user,
        "me.credentials_update",
        "user",
        user.id,
        {
            "cleared": cleared,
            "key_changed": bool((payload.api_key or "").strip()),
            "base_url": record.base_url if record else None,
        },
    )
    db.commit()
    # Env and settings.yaml are baked into the container, so a restart is needed;
    # the instance service recreates the container when the config hash changes.
    response.headers["X-Needs-Restart"] = "true"
    return envelope(request, provider.view(record))


@router.get("/announcements")
def announcements(
    request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    from .announcements import my_announcements

    return my_announcements(request, user, db)
