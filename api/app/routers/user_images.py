from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from sqlalchemy.orm import Session

from ..audit import write_audit
from ..config import settings
from ..db import get_db
from ..deps import csrf_guard, current_user
from ..errors import ApiError, envelope
from ..models import DshImage, User, UserImage
from ..schemas import ImageCommitIn, VersionSwitchIn
from ..services import user_images as service
from ..services.instances import get_instance

router = APIRouter(tags=["user-images"], dependencies=[Depends(csrf_guard)])


def _view(image: UserImage) -> dict:
    return {
        "id": image.id,
        "name": image.name,
        "image_ref": image.image_ref,
        "size_bytes": image.size_bytes,
        "base_version": image.base_version,
        "status": image.status.value,
        "note": image.note,
        "error": image.error,
        "created_at": image.created_at.isoformat() + "Z",
    }


@router.get("/me/images")
def list_images(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(UserImage).filter(UserImage.user_id == user.id).order_by(UserImage.id.desc()).all()
    )
    return envelope(request, {"items": [_view(i) for i in rows], "total": len(rows)})


@router.post("/instances/me/images", status_code=202)
def commit_image(
    payload: ImageCommitIn,
    request: Request,
    background: BackgroundTasks,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    instance = get_instance(db, user.id)
    if instance is None:
        raise ApiError("INSTANCE_NOT_FOUND", "还没有实例", 404)
    image = service.start_commit(db, user, instance, payload.name, payload.note)
    write_audit(db, request, user, "image.commit", "user_image", image.id, {"name": image.name})
    db.commit()
    if settings.jobs_inline:
        service.finish_commit(image.id)
        db.refresh(image)
    else:
        background.add_task(service.finish_commit, image.id)
    return envelope(request, _view(image))


@router.delete("/me/images/{image_id}", status_code=204)
def delete_image(
    image_id: int,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    image = db.get(UserImage, image_id)
    if image is None or image.user_id != user.id:
        raise ApiError("NOT_FOUND", "镜像不存在", 404)
    instance = get_instance(db, user.id)
    if instance is not None and instance.user_image_id == image.id:
        raise ApiError("IMAGE_IN_USE", "当前实例正在使用该镜像", 409)
    write_audit(db, request, user, "image.delete", "user_image", image.id)
    db.delete(image)
    db.commit()
    return None


@router.post("/me/images/{image_id}/rebuild", status_code=202)
def rebuild(
    image_id: int,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    image = db.get(UserImage, image_id)
    if image is None or image.user_id != user.id:
        raise ApiError("NOT_FOUND", "镜像不存在", 404)
    instance = get_instance(db, user.id)
    if instance is None:
        raise ApiError("INSTANCE_NOT_FOUND", "还没有实例", 404)
    write_audit(db, request, user, "image.rebuild", "user_image", image.id)
    db.commit()
    service.rebuild(db, user, instance, image)
    return envelope(request, {"status": "starting"})


@router.post("/instances/me/version", status_code=202)
def switch_version(
    payload: VersionSwitchIn,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    instance = get_instance(db, user.id)
    if instance is None:
        raise ApiError("INSTANCE_NOT_FOUND", "还没有实例", 404)
    image = db.get(DshImage, payload.image_id)
    if image is None or image.status.value != "ready":
        raise ApiError("IMAGE_DISABLED", "该版本不可用", 409)
    write_audit(
        db,
        request,
        user,
        "instance.switch_version",
        "instance",
        instance.id,
        {"image_id": image.id},
    )
    db.commit()
    service.switch_version(db, user, instance, image.id)
    return envelope(request, {"status": "starting", "version": image.version})
