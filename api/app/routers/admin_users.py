from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..audit import write_audit
from ..config import settings
from ..db import get_db
from ..deps import client_ip, csrf_guard, require_admin
from ..errors import ApiError, envelope
from ..models import Instance, Plan, Role, User, UserQuota, UserSession, UserStatus
from ..pagination import Page, paginated
from ..schemas import ResetPasswordIn, UserCreateIn, UserPatchIn, iso
from ..security import hash_password, utcnow
from ..services.instances import apply_plan, restart_instance
from ..services.quota import current_cycle, effective

router = APIRouter(prefix="/admin/users", tags=["admin:users"], dependencies=[Depends(csrf_guard)])


def _row(db: Session, user: User) -> dict:
    quota = db.get(UserQuota, user.id)
    plan = db.get(Plan, quota.plan_id) if quota and quota.plan_id else user.plan
    q = effective(quota, plan) if quota else {}
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "role": user.role.value,
        "status": user.status.value,
        "subdomain": user.subdomain,
        "must_change_password": user.must_change_password,
        "plan": {"id": plan.id, "name": plan.name, "code": plan.code} if plan else None,
        "quota": {
            "used_minutes": q.get("used_minutes", 0),
            "remaining_minutes": q.get("remaining_minutes", 0),
            "total_minutes": q.get("total_minutes", 0),
            "cpu_cores": q.get("cpu_cores"),
            "memory_mb": q.get("memory_mb"),
            "disk_gb": q.get("disk_gb"),
        },
        "instance": None,
        "created_at": iso(user.created_at),
        "last_login_at": iso(user.last_login_at),
        "last_login_ip": user.last_login_ip,
        "disabled_reason": user.disabled_reason,
    }


@router.get("")
def list_users(
    request: Request,
    q: str | None = Query(default=None, max_length=100),
    status: str | None = Query(default=None),
    plan_id: int | None = Query(default=None),
    page: Page = Depends(),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = db.query(User).filter(User.deleted_at.is_(None))
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                User.username.like(like),
                User.display_name.like(like),
                User.email.like(like),
            )
        )
    if status in {"active", "disabled", "pending"}:
        query = query.filter(User.status == UserStatus(status))
    if plan_id:
        query = query.filter(User.plan_id == plan_id)
    total = query.count()
    rows = query.order_by(User.id.desc()).offset(page.offset).limit(page.page_size).all()
    return envelope(request, paginated([_row(db, u) for u in rows], total, page))


@router.post("", status_code=201)
def create_user(
    payload: UserCreateIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if db.query(User.id).filter(User.username == payload.username).first():
        raise ApiError("USERNAME_TAKEN", "用户名已被占用", 409)
    if payload.email and db.query(User.id).filter(User.email == str(payload.email)).first():
        raise ApiError("EMAIL_TAKEN", "邮箱已被使用", 409)
    plan = None
    if payload.plan_id:
        plan = db.get(Plan, payload.plan_id)
        if plan is None:
            raise ApiError("PLAN_NOT_FOUND", "套餐不存在", 404)
    else:
        plan = db.query(Plan).filter(Plan.is_default.is_(True)).first()

    user = User(
        username=payload.username,
        subdomain=payload.username,
        display_name=payload.display_name or payload.username,
        email=str(payload.email) if payload.email else None,
        password_hash=hash_password(payload.password),
        role=Role(payload.role),
        status=UserStatus.active,
        plan_id=plan.id if plan else None,
        must_change_password=payload.must_change_password,
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
    write_audit(
        db,
        request,
        admin,
        "admin.user_create",
        "user",
        user.id,
        {"username": user.username, "role": user.role.value},
    )
    db.commit()
    return envelope(request, _row(db, user))


@router.get("/{user_id}")
def get_user(
    user_id: int,
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise ApiError("NOT_FOUND", "用户不存在", 404)
    data = _row(db, user)
    data["sessions"] = (
        db.query(UserSession)
        .filter(
            UserSession.user_id == user.id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > utcnow(),
        )
        .count()
    )
    return envelope(request, data)


@router.patch("/{user_id}")
def patch_user(
    user_id: int,
    payload: UserPatchIn,
    request: Request,
    background: BackgroundTasks,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise ApiError("NOT_FOUND", "用户不存在", 404)

    changes: dict = {}
    restart_id: int | None = None
    if payload.plan_id is not None and payload.plan_id != 0:
        plan = db.get(Plan, payload.plan_id)
        if plan is None:
            raise ApiError("PLAN_NOT_FOUND", "套餐不存在", 404)
        user.plan_id = plan.id
        quota = db.get(UserQuota, user.id)
        if quota:
            quota.plan_id = plan.id
        changes["plan_id"] = plan.id
        # The instance keeps the shape it was created with, and Docker cannot
        # resize a live container — without this the new plan only showed up in
        # the quota card while the container kept running with the old limits.
        instance = db.query(Instance).filter(Instance.user_id == user.id).first()
        if instance is not None and apply_plan(
            db,
            instance,
            cpu_cores=float(plan.cpu_cores),
            memory_mb=plan.memory_mb,
            disk_gb=plan.disk_gb,
        ):
            changes["instance_spec"] = f"{plan.cpu_cores}C/{plan.memory_mb}MB"
            if instance.container_id:
                restart_id = instance.id
                changes["instance_restarted"] = True
    if payload.display_name is not None:
        user.display_name = payload.display_name
        changes["display_name"] = payload.display_name
    if payload.email is not None:
        exists = (
            db.query(User.id).filter(User.email == str(payload.email), User.id != user.id).first()
        )
        if exists:
            raise ApiError("EMAIL_TAKEN", "邮箱已被使用", 409)
        user.email = str(payload.email)
        changes["email"] = user.email

    if payload.role is not None and payload.role != user.role.value:
        if user.role == Role.admin and payload.role != "admin":
            admin_count = (
                db.query(func.count(User.id))
                .filter(User.role == Role.admin, User.deleted_at.is_(None))
                .scalar()
            )
            if admin_count <= 1:
                raise ApiError("LAST_ADMIN", "不能降级最后一个管理员", 409)
        user.role = Role(payload.role)
        changes["role"] = user.role.value

    if payload.status is not None and payload.status != user.status.value:
        if payload.status == "disabled":
            if user.id == admin.id:
                raise ApiError("FORBIDDEN", "不能禁用自己的账号", 403)
            if user.role == Role.admin:
                admin_count = (
                    db.query(func.count(User.id))
                    .filter(User.role == Role.admin, User.deleted_at.is_(None))
                    .scalar()
                )
                if admin_count <= 1:
                    raise ApiError("LAST_ADMIN", "不能禁用最后一个管理员", 409)
            user.status = UserStatus.disabled
            user.disabled_reason = payload.reason or "管理员禁用"
            db.query(UserSession).filter(
                UserSession.user_id == user.id, UserSession.revoked_at.is_(None)
            ).update({UserSession.revoked_at: utcnow()})
        elif payload.status == "active":
            user.status = UserStatus.active
            user.disabled_reason = None
        else:
            user.status = UserStatus.pending
        changes["status"] = user.status.value

    if changes:
        write_audit(db, request, admin, "admin.user_update", "user", user.id, changes)
    db.commit()
    if restart_id is not None:
        # Re-create the container so the new limits are real, not just displayed.
        if settings.jobs_inline:
            restart_instance(restart_id, admin.id)
        else:
            background.add_task(restart_instance, restart_id, admin.id)
    return envelope(request, _row(db, user))


@router.post("/{user_id}/reset-password", status_code=204)
def reset_password(
    user_id: int,
    payload: ResetPasswordIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise ApiError("NOT_FOUND", "用户不存在", 404)
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = True
    db.query(UserSession).filter(
        UserSession.user_id == user.id, UserSession.revoked_at.is_(None)
    ).update({UserSession.revoked_at: utcnow()})
    write_audit(db, request, admin, "admin.user_reset_password", "user", user.id)
    db.commit()


@router.delete("/{user_id}", status_code=204)
def delete_user(
    user_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise ApiError("NOT_FOUND", "用户不存在", 404)
    if user.id == admin.id:
        raise ApiError("FORBIDDEN", "不能删除自己的账号", 403)
    if user.role == Role.admin:
        admin_count = (
            db.query(func.count(User.id))
            .filter(User.role == Role.admin, User.deleted_at.is_(None))
            .scalar()
        )
        if admin_count <= 1:
            raise ApiError("LAST_ADMIN", "不能删除最后一个管理员", 409)

    user.deleted_at = utcnow()
    user.status = UserStatus.disabled
    suffix = f"__deleted_{user.id}_{int(datetime.utcnow().timestamp())}"
    user.username = (user.username[: 32 - len(suffix)] + suffix)[:32]
    user.subdomain = (user.subdomain[: 63 - len(suffix)] + suffix)[:63]
    user.email = None
    db.query(UserSession).filter(
        UserSession.user_id == user.id, UserSession.revoked_at.is_(None)
    ).update({UserSession.revoked_at: utcnow()})
    write_audit(
        db,
        request,
        admin,
        "admin.user_delete",
        "user",
        user_id,
        {"username": user.username, "ip": client_ip(request)},
    )
    db.commit()
