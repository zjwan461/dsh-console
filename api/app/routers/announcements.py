from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import csrf_guard, current_user, require_admin
from ..errors import ApiError, envelope
from ..models import Announcement, User
from ..schemas import AnnouncementIn

router = APIRouter(tags=["announcements"], dependencies=[Depends(csrf_guard)])


def _view(item: Announcement) -> dict:
    return {
        "id": item.id,
        "title": item.title,
        "body_md": item.body_md,
        "level": item.level,
        "status": item.status,
        "pinned": item.pinned,
        "publish_at": item.publish_at.isoformat() + "Z" if item.publish_at else None,
        "expire_at": item.expire_at.isoformat() + "Z" if item.expire_at else None,
        "created_at": item.created_at.isoformat() + "Z",
    }


@router.get("/me/announcements")
def my_announcements(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    now = datetime.utcnow()
    rows = (
        db.query(Announcement)
        .filter(
            Announcement.status == "published",
            Announcement.publish_at.is_(None) | (Announcement.publish_at <= now),
            Announcement.expire_at.is_(None) | (Announcement.expire_at > now),
        )
        .order_by(Announcement.pinned.desc(), Announcement.id.desc())
        .all()
    )
    return envelope(request, {"items": [_view(a) for a in rows], "total": len(rows)})


@router.get("/admin/announcements")
def list_announcements(
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rows = db.query(Announcement).order_by(Announcement.id.desc()).all()
    return envelope(request, {"items": [_view(a) for a in rows], "total": len(rows)})


@router.post("/admin/announcements", status_code=201)
def create_announcement(
    payload: AnnouncementIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    item = Announcement(**payload.model_dump(), created_by=admin.id)
    db.add(item)
    db.commit()
    db.refresh(item)
    return envelope(request, _view(item))


@router.patch("/admin/announcements/{announcement_id}")
def patch_announcement(
    announcement_id: int,
    payload: AnnouncementIn,
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    item = db.get(Announcement, announcement_id)
    if item is None:
        raise ApiError("NOT_FOUND", "公告不存在", 404)
    for key, value in payload.model_dump().items():
        setattr(item, key, value)
    db.commit()
    return envelope(request, _view(item))


@router.post("/admin/announcements/{announcement_id}/publish")
def publish(
    announcement_id: int,
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    item = db.get(Announcement, announcement_id)
    if item is None:
        raise ApiError("NOT_FOUND", "公告不存在", 404)
    item.status = "published"
    if item.publish_at is None:
        item.publish_at = datetime.utcnow()
    db.commit()
    return envelope(request, _view(item))


@router.delete("/admin/announcements/{announcement_id}", status_code=204)
def delete_announcement(
    announcement_id: int,
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    item = db.get(Announcement, announcement_id)
    if item is None:
        raise ApiError("NOT_FOUND", "公告不存在", 404)
    db.delete(item)
    db.commit()
    return None
