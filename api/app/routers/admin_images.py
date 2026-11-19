from __future__ import annotations

import os

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Request, UploadFile
from sqlalchemy.orm import Session

from ..audit import write_audit
from ..config import settings
from ..db import get_db
from ..deps import csrf_guard, require_admin
from ..drivers import DriverError, get_driver
from ..errors import ApiError, envelope
from ..models import DshImage, ImageStatus, Instance, User
from ..schemas import ImagePatchIn, ImageRegisterIn, RemoteImportIn
from ..services import caddy
from ..services import image_catalog as catalog

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
    try:
        get_driver().pull(payload.image_ref)
        info = get_driver().image_info(payload.image_ref)
    except DriverError as exc:
        raise ApiError("IMAGE_IMPORT_FAILED", str(exc), 422) from exc
    image = catalog.register(
        db,
        image_ref=payload.image_ref,
        version=payload.version,
        changelog=payload.changelog,
        is_default=payload.is_default,
        source="pull",
        info=info,
        actor_id=admin.id,
    )
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
    try:
        info = get_driver().image_info(payload.image_ref)
    except DriverError as exc:
        raise ApiError("IMAGE_IMPORT_FAILED", str(exc), 422) from exc
    image = catalog.register(
        db,
        image_ref=payload.image_ref,
        version=payload.version,
        changelog=payload.changelog,
        is_default=payload.is_default,
        source="local",
        info=info,
        actor_id=admin.id,
    )
    write_audit(
        db, request, admin, "admin.image_register", "image", image.id, {"ref": image.image_ref}
    )
    db.commit()
    return envelope(request, image_dict(image))


@router.get("/remote")
def remote_images(
    request: Request,
    refresh: bool = False,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Published tags from the dsh-docker registry, merged with what we have."""
    return envelope(request, catalog.catalog(db, refresh=refresh))


@router.post("/remote/check")
def check_remote(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    result = catalog.catalog(db, refresh=True)
    write_audit(
        db,
        request,
        admin,
        "admin.image_check_registry",
        "image_catalog",
        result.get("repository", ""),
        {"tags": len(result.get("items") or []), "error": result.get("error")},
    )
    db.commit()
    return envelope(request, result)


@router.post("/remote/import", status_code=202)
def import_remote(
    payload: RemoteImportIn,
    request: Request,
    background: BackgroundTasks,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Reserve the version now, pull it in the background.

    A ~1.3 GB pull cannot finish inside one HTTP request without the UI looking
    frozen, so the row is created in `importing` state and the client polls the
    image list for the outcome.
    """
    image = catalog.start_import(
        db, payload.tag, is_default=payload.is_default, changelog=payload.changelog
    )
    write_audit(
        db,
        request,
        admin,
        "admin.image_import_remote",
        "image",
        image.id,
        {"tag": payload.tag, "ref": image.image_ref},
    )
    db.commit()
    if settings.jobs_inline:
        catalog.finish_import(image.id)
        db.refresh(image)
    else:
        background.add_task(catalog.finish_import, image.id)
    return envelope(request, image_dict(image))


@router.post("/upload", status_code=201)
def upload_image(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    file: UploadFile = File(...),
    version: str = Form(default=""),
    changelog: str = Form(default=""),
    is_default: bool = Form(default=False),
):
    """Import a `docker save` tarball.

    The tar is written to DATA_ROOT and handed to the daemon by the API container;
    the file is removed afterwards. Big files therefore need room under
    IMAGE_UPLOAD_MAX_MB, and the request itself is streamed.
    """
    limit = settings.image_upload_max_mb * 1024 * 1024
    path = catalog.staging_path()
    size = 0
    try:
        with open(path, "wb") as out:
            while chunk := file.file.read(4 * 1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise ApiError(
                        "FILE_TOO_LARGE",
                        f"tar 包超过上限（{settings.image_upload_max_mb} MB）",
                        413,
                    )
                out.write(chunk)
        image = catalog.import_tar(
            db,
            path,
            version=version,
            changelog=changelog,
            is_default=is_default,
            actor_id=admin.id,
        )
    finally:
        if os.path.exists(path):
            os.remove(path)
    write_audit(
        db,
        request,
        admin,
        "admin.image_import_tar",
        "image",
        image.id,
        {"ref": image.image_ref, "bytes": size},
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
