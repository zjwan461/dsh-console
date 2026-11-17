from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from ..audit import write_audit
from ..db import get_db
from ..deps import csrf_guard, require_admin
from ..errors import ApiError, envelope
from ..models import CredentialScope, ProviderCredential, User
from ..schemas import CredentialIn, SettingsIn
from ..services import provider
from ..services.settings import all_settings, put_setting

router = APIRouter(prefix="/admin", tags=["admin:settings"], dependencies=[Depends(csrf_guard)])

DOMAIN_RE = re.compile(r"^(?=.{3,190}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


@router.get("/settings")
def get_settings(
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return envelope(request, all_settings(db))


@router.put("/settings")
def update_settings(
    payload: SettingsIn,
    request: Request,
    response: Response,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    changes: dict[str, Any] = payload.model_dump(exclude_unset=True)
    if "base_domain" in changes:
        domain = (changes["base_domain"] or "").strip().lower()
        if not DOMAIN_RE.match(domain):
            raise ApiError("VALIDATION_ERROR", "基础域名格式不正确", 422)
        changes["base_domain"] = domain
    for key, value in changes.items():
        put_setting(db, key, value, admin.id)
    write_audit(db, request, admin, "admin.settings_update", "settings", "", changes)
    db.commit()
    if "base_domain" in changes:
        response.headers["X-Needs-Caddy-Reload"] = "true"
    return envelope(request, all_settings(db))


def _platform_credential(db: Session) -> ProviderCredential | None:
    return provider.find(db, CredentialScope.platform, 0)


@router.get("/credentials/platform")
def get_platform_credential(
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return envelope(request, provider.view(_platform_credential(db)))


@router.put("/credentials/platform")
def put_platform_credential(
    payload: CredentialIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    cred, cleared = provider.save(
        db,
        scope=CredentialScope.platform,
        user_id=0,
        api_key=payload.api_key,
        base_url=payload.base_url,
        actor_id=admin.id,
        clear_key=payload.clear_key,
    )
    write_audit(
        db,
        request,
        admin,
        "admin.platform_key_update",
        "provider_credential",
        "deepseek",
        {
            "cleared": cleared,
            "key_changed": bool((payload.api_key or "").strip()),
            "base_url": cred.base_url if cred else None,
        },
    )
    db.commit()
    return envelope(request, provider.view(cred))
