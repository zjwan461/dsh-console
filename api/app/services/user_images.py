from __future__ import annotations

import logging
import re
import time

from sqlalchemy.orm import Session

from ..drivers import DriverError, get_driver
from ..errors import ApiError
from ..models import Instance, Plan, User, UserImage, UserImageStatus
from .instances import start_instance

log = logging.getLogger("dsh.user-images")

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,30}$")


def start_commit(
    db: Session, user: User, instance: Instance, name: str, note: str = ""
) -> UserImage:
    """Validate and reserve the row; the commit itself runs out of band.

    `docker commit` on a multi-GB container takes minutes, so the HTTP request
    used to hang with nothing but a spinner. The row now appears immediately as
    `committing`, the UI polls it, and a duplicate name is rejected right away.
    """
    if not NAME_RE.match(name):
        raise ApiError("VALIDATION_ERROR", "镜像名需为 2-31 位小写字母/数字/连字符", 422)
    if instance.container_id is None:
        raise ApiError("INSTANCE_NOT_FOUND", "实例没有容器，无法保存镜像", 409)
    plan = db.get(Plan, user.plan_id) if user.plan_id else None
    limit = plan.max_user_images if plan else 2
    used = db.query(UserImage).filter(UserImage.user_id == user.id).count()
    if used >= limit:
        raise ApiError("IMAGE_LIMIT", "个人镜像数量已达上限", 409)
    if db.query(UserImage.id).filter(UserImage.user_id == user.id, UserImage.name == name).first():
        raise ApiError("IMAGE_DUPLICATE", "同名镜像已存在", 409)

    tag = f"dsh-user/{user.id}:{name}-{int(time.time())}"
    image = UserImage(
        user_id=user.id,
        instance_id=instance.id,
        name=name,
        image_ref=tag,
        base_version=instance.dsh_version,
        status=UserImageStatus.committing,
        note=note[:200],
    )
    db.add(image)
    try:
        db.commit()
    except Exception:  # noqa: BLE001 - unique constraint race on the name
        db.rollback()
        raise ApiError("IMAGE_DUPLICATE", "同名镜像已存在", 409) from None
    db.refresh(image)
    return image


def finish_commit(image_id: int) -> None:
    """Background half: commit the container, then record success or failure."""
    from ..db import SessionLocal

    with SessionLocal() as db:
        image = db.get(UserImage, image_id)
        if image is None:
            return
        instance = db.get(Instance, image.instance_id)
        if instance is None or instance.container_id is None:
            image.status = UserImageStatus.failed
            image.error = "实例容器已不存在"
            db.commit()
            return
        changes = [
            "ENV DEEPSEEK_API_KEY=",
            "ENV LITELLM_API_KEY=",
            "LABEL dsh.console.user_image=true",
            f"LABEL dsh.console.owner={image.user_id}",
            f"LABEL dsh.console.base_version={image.base_version}",
        ]
        try:
            digest = get_driver().commit(instance.container_id, image.image_ref, changes)
            info = get_driver().image_info(image.image_ref)
        except Exception as exc:  # noqa: BLE001 - recorded, not raised
            image.status = UserImageStatus.failed
            image.error = str(exc)[:500]
            db.commit()
            log.warning("image commit failed for %s: %s", image.image_ref, exc)
            return
        image.status = UserImageStatus.ready
        image.error = None
        image.digest = info.get("digest") or digest
        image.size_bytes = info.get("size", 0)
        db.commit()
        log.info("image commit finished: %s", image.image_ref)


def commit(db: Session, user: User, instance: Instance, name: str, note: str = "") -> UserImage:
    """Synchronous commit (used where a caller needs the finished row)."""
    image = start_commit(db, user, instance, name, note)
    finish_commit(image.id)
    db.refresh(image)
    if image.status != UserImageStatus.ready:
        raise ApiError("IMAGE_COMMIT_FAILED", image.error or "保存镜像失败", 422)
    return image


def rebuild(db: Session, user: User, instance: Instance, image: UserImage) -> None:
    if image.user_id != user.id:
        raise ApiError("NOT_FOUND", "镜像不存在", 404)
    driver = get_driver()
    if instance.container_id:
        try:
            driver.stop(instance.container_id)
            driver.remove(instance.container_id)
        except DriverError:
            log.warning("could not remove old container", exc_info=True)
    instance.container_id = None
    instance.user_image_id = image.id
    instance.dsh_version = image.base_version
    db.commit()
    start_instance(instance.id, user.id)


def switch_version(db: Session, user: User, instance: Instance, image_id: int) -> None:
    from ..models import DshImage

    image = db.get(DshImage, image_id)
    if image is None:
        raise ApiError("IMAGE_NOT_FOUND", "版本不存在", 404)
    driver = get_driver()
    if instance.container_id:
        try:
            driver.stop(instance.container_id)
            driver.remove(instance.container_id)
        except DriverError:
            log.warning("could not remove old container", exc_info=True)
    instance.container_id = None
    instance.image_id = image.id
    instance.user_image_id = None
    instance.dsh_version = image.version
    db.commit()
    start_instance(instance.id, user.id)
