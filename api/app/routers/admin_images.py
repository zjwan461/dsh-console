from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..audit import write_audit
from ..db import get_db
from ..deps import csrf_guard, require_admin
from ..drivers import DriverError, get_driver
from ..errors import ApiError, envelope
from ..models import DshImage, ImageStatus, Instance, User
from ..schemas import ImagePatchIn, ImageRegisterIn
from ..services import caddy

router = APIRouter(
    prefix="/admin/images", tags=["admin:images"], dependencies=[Depends(csrf_guard)]
)


def image_dict(image: DshImage) -> dict:
    return {
        "id": image.id,
        "version": image.version,
        "image_ref": image.image_ref,
        "digest": image.digest,
        "size_bytes": image.size_bytes,
        "arch": image.arch,
        "source": image.source,
        "status": image.status.value,
        "is_default": image.is_default,
        "changelog": image.changelog,
        "error": image.error,
        "created_at": image.created_at.isoformat() + "Z",
    }


@router.get("")
def list_images(
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rows = db.query(DshImage).order_by(DshImage.id.desc()).all()
    return envelope(request, {"items": [image_dict(i) for i in rows], "total": len(rows)})


@router.post("/pull", status_code=201)
def pull_image(
    payload: ImageRegisterIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if db.query(DshImage.id).filter(DshImage.image_ref == payload.image_ref).first():
        raise ApiError("IMAGE_DUPLICATE", "该镜像已登记", 409)
    try:
        get_driver().pull(payload.image_ref)
        info = get_driver().image_info(payload.image_ref)
    except DriverError as exc:
        raise ApiError("IMAGE_IMPORT_FAILED", str(exc), 422) from exc
    if payload.is_default:
        db.query(DshImage).filter(DshImage.is_default.is_(True)).update(
            {DshImage.is_default: False}
        )
    image = DshImage(
        version=payload.version,
        image_ref=payload.image_ref,
        digest=info.get("digest"),
        size_bytes=info.get("size", 0),
        arch=info.get("arch", "amd64"),
        source="pull",
        status=ImageStatus.ready,
        is_default=payload.is_default,
        changelog=payload.changelog,
        labels=info.get("labels"),
        created_by=admin.id,
    )
    db.add(image)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiError("IMAGE_DUPLICATE", "该镜像已登记", 409) from None
    write_audit(db, request, admin, "admin.image_pull", "image", image.id, {"ref": image.image_ref})
    db.commit()
    return envelope(request, image_dict(image))


@router.post("", status_code=201)
def register_image(
    payload: ImageRegisterIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if db.query(DshImage.id).filter(DshImage.image_ref == payload.image_ref).first():
        raise ApiError("IMAGE_DUPLICATE", "该镜像已登记", 409)
    try:
        info = get_driver().image_info(payload.image_ref)
    except DriverError as exc:
        raise ApiError("IMAGE_IMPORT_FAILED", str(exc), 422) from exc
    if payload.is_default:
        db.query(DshImage).filter(DshImage.is_default.is_(True)).update(
            {DshImage.is_default: False}
        )
    image = DshImage(
        version=payload.version,
        image_ref=payload.image_ref,
        digest=info.get("digest"),
        size_bytes=info.get("size", 0),
        arch=info.get("arch", "amd64"),
        source="pull",
        status=ImageStatus.ready,
        is_default=payload.is_default,
        changelog=payload.changelog,
        labels=info.get("labels"),
        created_by=admin.id,
    )
    db.add(image)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiError("IMAGE_DUPLICATE", "该镜像已登记", 409) from None
    write_audit(
        db, request, admin, "admin.image_register", "image", image.id, {"ref": image.image_ref}
    )
    db.commit()
    return envelope(request, image_dict(image))


@router.patch("/{image_id}")
def patch_image(
    image_id: int,
    payload: ImagePatchIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    image = db.get(DshImage, image_id)
    if image is None:
        raise ApiError("IMAGE_NOT_FOUND", "镜像不存在", 404)
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("is_default") is True:
        db.query(DshImage).filter(DshImage.is_default.is_(True)).update(
            {DshImage.is_default: False}
        )
    if "status" in changes:
        image.status = ImageStatus(changes.pop("status"))
    for key, value in changes.items():
        setattr(image, key, value)
    write_audit(
        db,
        request,
        admin,
        "admin.image_update",
        "image",
        image.id,
        payload.model_dump(exclude_unset=True),
    )
    db.commit()
    return envelope(request, image_dict(image))


@router.delete("/{image_id}", status_code=204)
def delete_image(
    image_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    image = db.get(DshImage, image_id)
    if image is None:
        raise ApiError("IMAGE_NOT_FOUND", "镜像不存在", 404)
    in_use = db.query(Instance.id).filter(Instance.image_id == image.id).first()
    if in_use:
        raise ApiError("IMAGE_IN_USE", "仍有实例使用该镜像", 409)
    db.delete(image)
    write_audit(
        db, request, admin, "admin.image_delete", "image", image_id, {"ref": image.image_ref}
    )
    db.commit()


@router.post("/{image_id}/apply")
def apply_routes(
    image_id: int,
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Re-publish routes (useful after a Caddy restart)."""
    try:
        caddy.apply(db)
    except caddy.CaddyError as exc:
        raise ApiError("CADDY_UNAVAILABLE", str(exc), 503) from exc
    return envelope(request, {"ok": True})
