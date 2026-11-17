from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from ..audit import write_audit
from ..db import get_db
from ..deps import csrf_guard, require_admin
from ..errors import ApiError, envelope
from ..models import LedgerReason, Plan, QuotaLedger, User
from ..schemas import GrantIn, QuotaPatchIn
from ..services.quota import effective, ensure_quota

router = APIRouter(
    prefix="/admin/quotas", tags=["admin:quotas"], dependencies=[Depends(csrf_guard)]
)


def _ledger(db: Session, user_id: int, limit: int = 50) -> list[dict]:
    rows = (
        db.query(QuotaLedger)
        .filter(QuotaLedger.user_id == user_id)
        .order_by(QuotaLedger.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": row.id,
            "delta_seconds": row.delta_seconds,
            "reason": row.reason.value,
            "balance_after": row.balance_after,
            "note": row.note,
            "actor_id": row.actor_id,
            "created_at": row.created_at.isoformat() + "Z",
        }
        for row in rows
    ]


def _view(db: Session, user: User) -> dict:
    quota = ensure_quota(db, user)
    plan = db.get(Plan, quota.plan_id) if quota.plan_id else user.plan
    view = effective(quota, plan)
    view["user_id"] = user.id
    view["username"] = user.username
    view["plan"] = plan.name if plan else None
    view["plan_id"] = plan.id if plan else None
    view["overrides"] = {
        "cpu_cores": float(quota.cpu_cores_override) if quota.cpu_cores_override else None,
        "memory_mb": quota.memory_mb_override,
        "disk_gb": quota.disk_gb_override,
        "monthly_minutes": quota.monthly_minutes_override,
    }
    view["extra_seconds"] = quota.extra_seconds
    view["seconds_used"] = quota.seconds_used
    return view


@router.get("/{user_id}")
def get_quota(
    user_id: int,
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise ApiError("NOT_FOUND", "用户不存在", 404)
    data = _view(db, user)
    data["ledger"] = _ledger(db, user_id)
    return envelope(request, data)


@router.patch("/{user_id}")
def patch_quota(
    user_id: int,
    payload: QuotaPatchIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise ApiError("NOT_FOUND", "用户不存在", 404)
    quota = ensure_quota(db, user)
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(quota, key, value)
    write_audit(db, request, admin, "admin.quota_update", "user", user.id, changes)
    db.commit()
    return envelope(request, _view(db, user))


@router.post("/{user_id}/grant")
def grant(
    user_id: int,
    payload: GrantIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise ApiError("NOT_FOUND", "用户不存在", 404)
    quota = ensure_quota(db, user)
    delta_seconds = payload.minutes * 60
    plan = db.get(Plan, quota.plan_id) if quota.plan_id else user.plan
    total_seconds = (plan.monthly_minutes * 60 if plan else 0) + quota.extra_seconds + delta_seconds
    balance = total_seconds - quota.seconds_used
    if balance < 0 and payload.minutes > 0:
        raise ApiError("QUOTA_NEGATIVE", "调整后余额为负，请检查数值", 400)
    quota.extra_seconds += delta_seconds
    db.add(
        QuotaLedger(
            user_id=user.id,
            delta_seconds=delta_seconds,
            reason=LedgerReason.grant if delta_seconds >= 0 else LedgerReason.adjust,
            balance_after=balance,
            note=payload.reason,
            actor_id=admin.id,
        )
    )
    write_audit(
        db,
        request,
        admin,
        "admin.quota_grant",
        "user",
        user.id,
        {"minutes": payload.minutes, "reason": payload.reason},
    )
    db.commit()
    return envelope(request, _view(db, user))


@router.get("/{user_id}/ledger")
def ledger(
    user_id: int,
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return envelope(request, {"items": _ledger(db, user_id, limit)})
