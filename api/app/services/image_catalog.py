"""Bridge between the registry catalogue and the images we already know about.

Two data sources meet here:
  * `dsh_images`    — versions imported into this console (local Docker daemon)
  * the registry    — what the dsh-docker project has published

The admin page needs them merged: "these are available upstream, these you have,
this one is newer than your default".
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..config import settings
from ..drivers import DriverError, get_driver
from ..errors import ApiError
from ..models import DshImage, ImageStatus
from ..security import utcnow
from .registry import RegistryClient, RegistryError, RemoteTag, parse_repository, split_tag
from .settings import get_setting, put_setting

log = logging.getLogger("dsh.catalog")

STATE_KEY = "registry_state"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]*$")


def repository(db: Session) -> str:
    return str(get_setting(db, "dsh_image_repository", settings.dsh_image_repository))


def _state(db: Session) -> dict:
    value = get_setting(db, STATE_KEY, None)
    return value if isinstance(value, dict) else {}


def _save_state(db: Session, payload: dict) -> None:
    put_setting(db, STATE_KEY, payload, None)
    db.commit()


def _client(db: Session) -> RegistryClient:
    return RegistryClient(repository(db))


def _as_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _match(db: Session, host: str, repo: str, tag: RemoteTag) -> tuple[DshImage | None, str]:
    """Find the local record for a published tag.

    Returns (image, how) where `how` is "ref", "version" or "" — a record that was
    pulled from the same reference is preferred, but a hand-registered image with
    the same version still counts as "already imported".
    """
    full_ref = f"{host}/{repo}:{tag.tag}"
    by_ref = db.query(DshImage).filter(DshImage.image_ref == full_ref).first()
    if by_ref is not None:
        return by_ref, "ref"
    by_version = (
        db.query(DshImage).filter(DshImage.version == tag.version).order_by(DshImage.id).first()
    )
    if by_version is not None:
        return by_version, "version"
    return None, ""


def catalog(
    db: Session,
    *,
    refresh: bool = False,
    max_age_seconds: int | None = None,
    limit: int = 20,
) -> dict:
    state = _state(db)
    repo_ref = repository(db)
    max_age = (
        max_age_seconds
        if max_age_seconds is not None
        else settings.registry_check_interval_hours * 3600
    )
    checked_at = _as_datetime(state.get("checked_at"))
    fresh = bool(checked_at) and (utcnow() - checked_at) < timedelta(seconds=max_age)

    if fresh and not refresh and state.get("repository") == repo_ref:
        return _decorate(db, state)

    try:
        client = _client(db)
        candidates = client.candidates(limit=limit)
    except RegistryError as exc:
        # Keep serving the last good snapshot: a registry outage must not blank
        # the page. The error is surfaced so the UI can show it.
        stale = (
            _decorate(db, state)
            if state
            else {
                "repository": repo_ref,
                "checked_at": None,
                "items": [],
                "update_available": False,
                "latest_version": None,
            }
        )
        stale["error"] = str(exc)
        stale["stale"] = True
        return stale
    except Exception as exc:  # noqa: BLE001 - a probe must never 500 the page
        log.exception("registry probe failed unexpectedly")
        stale = _decorate(db, state) if state else {"repository": repo_ref, "items": []}
        stale["error"] = f"{type(exc).__name__}: {exc}"[:300]
        stale["stale"] = True
        return stale

    host, repo = parse_repository(repo_ref)
    payload = {
        "repository": repo_ref,
        "registry_host": host,
        "repo_path": repo,
        "checked_at": utcnow().isoformat() + "Z",
        "items": [tag.as_dict() for tag in candidates],
    }
    _save_state(db, payload)
    return _decorate(db, payload)


def _decorate(db: Session, state: dict) -> dict:
    host = (
        state.get("registry_host") or parse_repository(state.get("repository", repository(db)))[0]
    )
    repo = state.get("repo_path") or parse_repository(state.get("repository", repository(db)))[1]
    default = db.query(DshImage).filter(DshImage.is_default.is_(True)).first()
    items = []
    for raw in state.get("items", []):
        tag = RemoteTag(
            tag=raw["tag"],
            version=raw["version"],
            variant=raw.get("variant", "full"),
            digest=raw.get("digest"),
            platform_digest=raw.get("platform_digest"),
            size_bytes=int(raw.get("size_bytes") or 0),
            created_at=_as_datetime(raw.get("created_at")),
            platform=raw.get("platform"),
            labels=raw.get("labels") or {},
        )
        image, how = _match(db, host, repo, tag)
        items.append(
            {
                **raw,
                "image_ref": f"{host}/{repo}:{tag.tag}",
                "imported": image is not None,
                "matched_by": how,
                "image_id": image.id if image else None,
                "status": image.status.value if image else None,
                "is_default": bool(image and image.is_default),
                "local_size_bytes": image.size_bytes if image else 0,
            }
        )
    newest = next((item for item in items if item["variant"] == "full"), None)
    return {
        "repository": state.get("repository") or repository(db),
        "registry_host": host,
        "repo_path": repo,
        "checked_at": state.get("checked_at"),
        "items": items,
        "latest_version": newest["version"] if newest else None,
        "latest_tag": newest["tag"] if newest else None,
        "current_version": default.version if default else None,
        "update_available": bool(
            newest and (default is None or newest["version"] != default.version)
        ),
        "stale": False,
    }


def start_import(db: Session, tag: str, *, is_default: bool, changelog: str = "") -> DshImage:
    """Reserve a row in `importing` state so the pull can run out of band.

    Pulling a ~1.3 GB image takes minutes; keeping the HTTP request open meant the
    console showed a spinner with no progress, and a page refresh started a second
    pull. The row appears immediately, a duplicate click is rejected, and the
    status flips to `ready`/`failed` when the pull finishes.
    """
    state = _state(db)
    host = state.get("registry_host") or parse_repository(repository(db))[0]
    repo = state.get("repo_path") or parse_repository(repository(db))[1]
    full_ref = f"{host}/{repo}:{tag}"

    if db.query(DshImage).filter(DshImage.image_ref == full_ref).first():
        raise ApiError("IMAGE_DUPLICATE", "该版本已导入或正在导入", 409)

    parsed = split_tag(tag)
    version = parsed[0] if parsed else tag
    if not NAME_RE.match(f"{repo}:{tag}".replace(":", "_")):
        raise ApiError("VALIDATION_ERROR", "标签格式不正确", 422)

    if is_default:
        db.query(DshImage).filter(DshImage.is_default.is_(True)).update(
            {DshImage.is_default: False}
        )
    image = DshImage(
        version=version,
        image_ref=full_ref,
        source="pull",
        status=ImageStatus.importing,
        is_default=is_default,
        changelog=changelog or f"从 {repository(db)} 导入",
    )
    db.add(image)
    try:
        db.commit()
    except Exception:  # noqa: BLE001 - unique constraint race
        db.rollback()
        raise ApiError("IMAGE_DUPLICATE", "该版本已导入或正在导入", 409) from None
    db.refresh(image)
    return image


def finish_import(image_id: int) -> None:
    """Background half of `start_import`: pull, then record success or failure."""
    from ..db import SessionLocal

    with SessionLocal() as db:
        image = db.get(DshImage, image_id)
        if image is None:
            return
        driver = get_driver()
        try:
            driver.pull(image.image_ref)
            info = driver.image_info(image.image_ref)
        except Exception as exc:  # noqa: BLE001 - any failure is recorded, not raised
            image.status = ImageStatus.failed
            image.error = str(exc)[:500]
            db.commit()
            log.warning("image import failed for %s: %s", image.image_ref, exc)
            return
        image.status = ImageStatus.ready
        image.error = None
        image.digest = info.get("digest")
        image.size_bytes = info.get("size", 0)
        image.arch = info.get("arch", "amd64")
        image.labels = info.get("labels")
        db.commit()
        log.info("image import finished: %s", image.image_ref)
        try:
            catalog(db, refresh=True)
        except Exception:  # noqa: BLE001 - the image is usable either way
            log.warning("catalog refresh after import failed", exc_info=True)


def import_tag(db: Session, tag: str, *, is_default: bool, changelog: str = "") -> DshImage:
    """Pull one published tag into the local daemon and register it (synchronous)."""
    image = start_import(db, tag, is_default=is_default, changelog=changelog)
    finish_import(image.id)
    db.refresh(image)
    if image.status != ImageStatus.ready:
        raise ApiError("IMAGE_IMPORT_FAILED", image.error or "导入失败", 422)
    return image


def register(
    db: Session,
    *,
    image_ref: str,
    version: str,
    changelog: str,
    is_default: bool,
    source: str,
    info: dict | None = None,
    actor_id: int | None = None,
) -> DshImage:
    """Insert a DshImage, clearing any previous default so exactly one remains."""
    if db.query(DshImage.id).filter(DshImage.image_ref == image_ref).first():
        raise ApiError("IMAGE_DUPLICATE", "该镜像已登记", 409)
    info = info or {}
    if is_default:
        db.query(DshImage).filter(DshImage.is_default.is_(True)).update(
            {DshImage.is_default: False}
        )
    image = DshImage(
        version=version,
        image_ref=image_ref,
        digest=info.get("digest"),
        size_bytes=info.get("size", 0),
        arch=info.get("arch", "amd64"),
        source=source,
        status=ImageStatus.ready,
        is_default=is_default,
        changelog=changelog,
        labels=info.get("labels"),
        created_by=actor_id,
    )
    db.add(image)
    try:
        db.commit()
    except Exception:  # noqa: BLE001 - unique constraint races
        db.rollback()
        raise ApiError("IMAGE_DUPLICATE", "该镜像已登记", 409) from None
    db.refresh(image)
    return image


def import_tar(
    db: Session,
    path: str,
    *,
    version: str = "",
    changelog: str = "",
    is_default: bool = False,
    actor_id: int | None = None,
) -> DshImage:
    """`docker load` a tarball written by `docker save`, then register it."""
    driver = get_driver()
    try:
        refs = driver.load_image(path)
    except DriverError as exc:
        raise ApiError("IMAGE_IMPORT_FAILED", str(exc), 422) from exc
    if not refs:
        raise ApiError(
            "IMAGE_IMPORT_FAILED", "tar 包里没有可用的镜像标签（请用 docker save 导出）", 422
        )
    ref = refs[0]
    tag = ref.rsplit(":", 1)[-1] if ":" in ref else ref
    parsed = split_tag(tag)
    resolved = version or (parsed[0] if parsed else tag)
    try:
        info = driver.image_info(ref)
    except DriverError:
        info = {}
    return register(
        db,
        image_ref=ref,
        version=resolved,
        changelog=changelog or "从 tar 包导入",
        is_default=is_default,
        source="upload",
        info=info,
        actor_id=actor_id,
    )


def upload_dir() -> str:
    path = os.path.join(settings.data_root, "images", "upload")
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def staging_path(suffix: str = ".tar") -> str:
    return os.path.join(upload_dir(), f"{uuid.uuid4().hex}{suffix}")


def check_once(db: Session) -> dict:
    """Worker entry point: refresh the catalogue so the UI has a fresh answer."""
    if not settings.registry_check_enabled:
        return {}
    result = catalog(db, refresh=True)
    log.info(
        "registry %s: %d tag(s), latest=%s, update_available=%s",
        result.get("repository"),
        len(result.get("items") or []),
        result.get("latest_version"),
        result.get("update_available"),
    )
    return result


# Referenced by callers that only need the helper names.
__all__ = ["catalog", "check_once", "import_tag", "import_tar", "register", "staging_path"]
