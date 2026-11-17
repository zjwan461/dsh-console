from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..audit import write_audit
from ..db import get_db
from ..deps import csrf_guard, require_admin
from ..errors import ApiError, envelope
from ..models import Plan, User, UserQuota
from ..schemas import PlanIn, PlanPatchIn

router = APIRouter(prefix="/admin/plans", tags=["admin:plans"], dependencies=[Depends(csrf_guard)])


def plan_dict(plan: Plan) -> dict:
    return {
        "id": plan.id,
        "code": plan.code,
        "name": plan.name,
        "description": plan.description,
        "cpu_cores": float(plan.cpu_cores),
        "memory_mb": plan.memory_mb,
        "disk_gb": plan.disk_gb,
        "monthly_minutes": plan.monthly_minutes,
        "max_snapshots": plan.max_snapshots,
        "max_user_images": plan.max_user_images,
        "gpu_count": plan.gpu_count,
        "is_default": plan.is_default,
        "enabled": plan.enabled,
        "sort_order": plan.sort_order,
        "created_at": plan.created_at.isoformat() + "Z",
    }


def _clear_default(db: Session) -> None:
    db.query(Plan).filter(Plan.is_default.is_(True)).update({Plan.is_default: False})


@router.get("")
def list_plans(
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    plans = db.query(Plan).order_by(Plan.sort_order, Plan.id).all()
    return envelope(request, {"items": [plan_dict(p) for p in plans], "total": len(plans)})


@router.post("", status_code=201)
def create_plan(
    payload: PlanIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if db.query(Plan.id).filter(Plan.code == payload.code).first():
        raise ApiError("PLAN_EXISTS", "套餐编码已存在", 409)
    if payload.is_default:
        _clear_default(db)
    plan = Plan(**payload.model_dump())
    db.add(plan)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ApiError("PLAN_EXISTS", "套餐编码已存在", 409) from None
    write_audit(db, request, admin, "admin.plan_create", "plan", plan.id, {"code": plan.code})
    db.commit()
    return envelope(request, plan_dict(plan))


@router.patch("/{plan_id}")
def patch_plan(
    plan_id: int,
    payload: PlanPatchIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    plan = db.get(Plan, plan_id)
    if plan is None:
        raise ApiError("PLAN_NOT_FOUND", "套餐不存在", 404)
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("is_default") is True:
        _clear_default(db)
    for key, value in changes.items():
        setattr(plan, key, value)
    write_audit(db, request, admin, "admin.plan_update", "plan", plan.id, changes)
    db.commit()
    return envelope(request, plan_dict(plan))


@router.delete("/{plan_id}", status_code=204)
def delete_plan(
    plan_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    plan = db.get(Plan, plan_id)
    if plan is None:
        raise ApiError("PLAN_NOT_FOUND", "套餐不存在", 404)
    in_use = (
        db.query(User.id).filter(User.plan_id == plan.id, User.deleted_at.is_(None)).first()
        or db.query(UserQuota.user_id).filter(UserQuota.plan_id == plan.id).first()
    )
    if in_use:
        raise ApiError("PLAN_IN_USE", "仍有用户使用该套餐，请先迁移", 409)
    db.delete(plan)
    write_audit(db, request, admin, "admin.plan_delete", "plan", plan_id, {"code": plan.code})
    db.commit()
