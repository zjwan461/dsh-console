from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..audit import write_audit
from ..db import get_db
from ..deps import csrf_guard, require_admin
from ..errors import ApiError, envelope
from ..models import Invite, Role, User
from ..schemas import InviteIn
from ..security import new_token, utcnow

router = APIRouter(
    prefix="/admin/invites", tags=["admin:invites"], dependencies=[Depends(csrf_guard)]
)


def invite_dict(invite: Invite) -> dict:
    now = utcnow()
    if invite.revoked_at is not None:
        status = "revoked"
    elif invite.expires_at is not None and invite.expires_at <= now:
        status = "expired"
    elif invite.used_count >= invite.max_uses:
        status = "exhausted"
    else:
        status = "active"
    return {
        "id": invite.id,
        "code": invite.code,
        "role": invite.role.value,
        "max_uses": invite.max_uses,
        "used_count": invite.used_count,
        "status": status,
        "note": invite.note,
        "expires_at": invite.expires_at.isoformat() + "Z" if invite.expires_at else None,
        "created_at": invite.created_at.isoformat() + "Z",
    }


@router.get("")
def list_invites(
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rows = db.query(Invite).order_by(Invite.id.desc()).limit(200).all()
    return envelope(request, {"items": [invite_dict(i) for i in rows], "total": len(rows)})


@router.post("", status_code=201)
def create_invite(
    payload: InviteIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    invite = Invite(
        code="INV-" + new_token(16).replace("-", "").replace("_", "")[:26],
        role=Role(payload.role),
        max_uses=payload.max_uses,
        note=payload.note,
        created_by=admin.id,
        expires_at=utcnow() + timedelta(days=payload.expires_in_days),
    )
    db.add(invite)
    db.flush()
    write_audit(
        db,
        request,
        admin,
        "admin.invite_create",
        "invite",
        invite.id,
        {"max_uses": invite.max_uses},
    )
    db.commit()
    return envelope(request, invite_dict(invite))


@router.delete("/{invite_id}", status_code=204)
def revoke_invite(
    invite_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    invite = db.get(Invite, invite_id)
    if invite is None:
        raise ApiError("NOT_FOUND", "邀请码不存在", 404)
    invite.revoked_at = utcnow()
    write_audit(db, request, admin, "admin.invite_revoke", "invite", invite.id)
    db.commit()
