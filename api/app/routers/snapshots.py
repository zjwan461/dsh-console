from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from sqlalchemy.orm import Session

from ..audit import write_audit
from ..config import settings
from ..db import get_db
from ..deps import csrf_guard, current_user
from ..errors import ApiError, envelope
from ..models import Instance, Snapshot, SnapshotStatus, User
from ..schemas import SnapshotCreateIn
from ..services import snapshots as snapshot_service
from ..services.instances import get_instance

router = APIRouter(tags=["snapshots"], dependencies=[Depends(csrf_guard)])
log = logging.getLogger("dsh.api.snapshots")


def _owned(db: Session, user: User, snapshot_id: int) -> Snapshot:
    snapshot = db.get(Snapshot, snapshot_id)
    if snapshot is None or snapshot.user_id != user.id:
        raise ApiError("NOT_FOUND", "快照不存在", 404)
    return snapshot


def _view(snapshot: Snapshot) -> dict:
    return {
        "id": snapshot.id,
        "status": snapshot.status.value,
        "size_bytes": snapshot.size_bytes,
        "dsh_version": snapshot.dsh_version,
        "note": snapshot.note,
        "error": snapshot.error,
        "created_at": snapshot.created_at.isoformat() + "Z",
        "expires_at": snapshot.expires_at.isoformat() + "Z" if snapshot.expires_at else None,
    }


@router.get("/instances/me/snapshots")
def list_snapshots(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(Snapshot)
        .filter(Snapshot.user_id == user.id)
        .order_by(Snapshot.id.desc())
        .limit(100)
        .all()
    )
    return envelope(request, {"items": [_view(s) for s in rows], "total": len(rows)})


@router.post("/instances/me/snapshots", status_code=202)
def create_snapshot(
    payload: SnapshotCreateIn,
    request: Request,
    background: BackgroundTasks,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    instance = get_instance(db, user.id)
    if instance is None:
        raise ApiError("INSTANCE_NOT_FOUND", "还没有实例", 404)
    plan = user.plan
    limit = plan.max_snapshots if plan else 2
    count = (
        db.query(Snapshot)
        .filter(Snapshot.user_id == user.id, Snapshot.status != SnapshotStatus.failed)
        .count()
    )
    if count >= limit:
        raise ApiError("SNAPSHOT_LIMIT", "快照数量已达上限", 409)

    snapshot = Snapshot(
        user_id=user.id,
        instance_id=instance.id,
        status=SnapshotStatus.creating,
        dsh_version=instance.dsh_version,
        note=payload.note[:200],
    )
    db.add(snapshot)
    write_audit(db, request, user, "snapshot.create", "snapshot", snapshot.id)
    db.commit()
    db.refresh(snapshot)

    def run(snapshot_id: int) -> None:
        from ..db import SessionLocal

        with SessionLocal() as session:
            record = session.get(Snapshot, snapshot_id)
            target = session.get(Instance, instance.id)
            if record is None or target is None:
                return
            try:
                snapshot_service.create(session, target, payload.note, snapshot=record)
            except Exception:
                log.exception("snapshot job failed")

    if settings.jobs_inline:
        run(snapshot.id)
    else:
        background.add_task(run, snapshot.id)
    db.refresh(snapshot)
    return envelope(request, _view(snapshot))


@router.post("/snapshots/{snapshot_id}/restore", status_code=202)
def restore_snapshot(
    snapshot_id: int,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    snapshot = _owned(db, user, snapshot_id)
    if snapshot.status != SnapshotStatus.ready:
        raise ApiError("SNAPSHOT_FAILED", "该快照不可用", 409)
    write_audit(db, request, user, "snapshot.restore", "snapshot", snapshot.id)
    db.commit()
    try:
        snapshot_service.restore(db, snapshot)
    except ApiError:
        raise
    return envelope(request, {"status": "restored", "snapshot": _view(snapshot)})


@router.delete("/snapshots/{snapshot_id}", status_code=204)
def delete_snapshot(
    snapshot_id: int,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    snapshot = _owned(db, user, snapshot_id)
    write_audit(db, request, user, "snapshot.delete", "snapshot", snapshot.id)
    snapshot_service.delete(db, snapshot)
    return None
