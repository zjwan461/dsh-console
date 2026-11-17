from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from ..audit import write_audit
from ..config import settings
from ..db import get_db
from ..deps import csrf_guard, current_user
from ..errors import ApiError, envelope
from ..models import CredentialScope, ProviderCredential, User
from ..schemas import CredentialIn, ProfileIn
from ..security import encrypt_secret, mask_key, utcnow
from ..services.quota import quota_view
from ..services.settings import get_setting

router = APIRouter(prefix="/me", tags=["me"], dependencies=[Depends(csrf_guard)])


def _credential(db: Session, scope: CredentialScope, user_id: int):
    return (
        db.query(ProviderCredential)
        .filter(
            ProviderCredential.scope == scope,
            ProviderCredential.user_id == user_id,
            ProviderCredential.provider == "deepseek",
        )
        .first()
    )


@router.get("/overview")
def overview(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    quota = quota_view(db, user)
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
            "instance": None,
            "quota": quota,
            "base_domain": get_setting(db, "base_domain", settings.base_domain),
            "announcements": [],
            "unread_tickets": 0,
            "features": {
                "self_key": True,
                "version_switch": True,
                "files": False,
                "snapshots": False,
            },
        },
    )


@router.get("/usage")
def usage(request: Request, user: User = Depends(current_user)):
    return envelope(
        request,
        {
            "summary": {
                "active_seconds": 0,
                "cpu_seconds": 0,
                "mem_mb_seconds": 0,
                "disk_bytes_max": 0,
                "net_rx_bytes": 0,
                "net_tx_bytes": 0,
            },
            "series": [],
            "note": "usage metering ships in M3",
        },
    )


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
    platform = _credential(db, CredentialScope.platform, 0)
    own = _credential(db, CredentialScope.user, user.id)
    return envelope(
        request,
        {
            "platform_key_configured": platform is not None,
            "user_key": {
                "configured": own is not None,
                "hint": own.key_hint if own else "",
                "updated_at": own.updated_at.isoformat() + "Z" if own else None,
            },
            "effective": "user" if own else ("platform" if platform else "none"),
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
    own = _credential(db, CredentialScope.user, user.id)
    key = payload.api_key.strip()
    if not key:
        if own is not None:
            db.delete(own)
    else:
        if len(key) < 8:
            raise ApiError("VALIDATION_ERROR", "API Key 格式不正确", 422)
        if own is None:
            own = ProviderCredential(
                scope=CredentialScope.user,
                user_id=user.id,
                provider="deepseek",
                ciphertext=encrypt_secret(key),
                key_hint=mask_key(key),
                created_by=user.id,
            )
            db.add(own)
        else:
            own.ciphertext = encrypt_secret(key)
            own.key_hint = mask_key(key)
            own.updated_at = utcnow()
    write_audit(
        db,
        request,
        user,
        "me.credentials_update",
        "user",
        user.id,
        {"cleared": not key},
    )
    db.commit()
    response.headers["X-Needs-Restart"] = "true"
    return envelope(request, {"configured": bool(key), "hint": mask_key(key) if key else ""})


@router.get("/announcements")
def announcements(request: Request, user: User = Depends(current_user)):
    return envelope(request, {"items": []})
