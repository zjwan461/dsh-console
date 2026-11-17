from __future__ import annotations

import logging
import os
import re
import shutil
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..config import settings
from ..drivers import ContainerSpec, DriverError, get_driver
from ..errors import ApiError
from ..models import (
    CredentialScope,
    DshImage,
    ImageStatus,
    Instance,
    InstanceEvent,
    InstanceStatus,
    Plan,
    ProviderCredential,
    User,
)
from ..redis import get_redis
from ..security import decrypt_secret, new_token, utcnow
from . import caddy
from .capacity import admit, probe_capacity
from .quota import effective, ensure_quota
from .settings import get_setting

log = logging.getLogger("dsh.instances")

TOKEN_RE = re.compile(r"[?&]token=([A-Za-z0-9_-]{20,})")
TRANSITIONAL = {
    InstanceStatus.provisioning,
    InstanceStatus.starting,
    InstanceStatus.waking,
    InstanceStatus.stopping,
    InstanceStatus.deleting,
}
RUNNING = {InstanceStatus.running, InstanceStatus.sleeping, InstanceStatus.waking}


def instance_url(instance: Instance, db: Session) -> str:
    domain = get_setting(db, "base_domain", settings.base_domain)
    return f"{settings.public_scheme}://{instance.subdomain}.{domain}"


def log_event(
    db: Session,
    instance: Instance,
    event_type: str,
    from_status: InstanceStatus | None,
    to_status: InstanceStatus | None,
    message: str = "",
    actor_id: int | None = None,
) -> None:
    db.add(
        InstanceEvent(
            instance_id=instance.id,
            type=event_type,
            from_status=from_status.value if from_status else None,
            to_status=to_status.value if to_status else None,
            message=message[:500],
            actor_id=actor_id,
        )
    )


def get_instance(db: Session, user_id: int) -> Instance | None:
    return (
        db.query(Instance)
        .filter(Instance.user_id == user_id, Instance.status != InstanceStatus.deleted)
        .first()
    )


def _ensure_dirs(instance: Instance) -> None:
    for path in (instance.data_path, instance.workspace_path):
        os.makedirs(path, mode=0o700, exist_ok=True)
        if hasattr(os, "chown"):
            try:
                os.chown(path, settings.instance_uid, settings.instance_gid)
            except PermissionError:  # dev on Windows / non-root
                pass


def _effective_key(db: Session, user_id: int) -> str | None:
    own = (
        db.query(ProviderCredential)
        .filter(
            ProviderCredential.scope == CredentialScope.user,
            ProviderCredential.user_id == user_id,
            ProviderCredential.provider == "deepseek",
        )
        .first()
    )
    platform = (
        db.query(ProviderCredential)
        .filter(
            ProviderCredential.scope == CredentialScope.platform,
            ProviderCredential.user_id == 0,
            ProviderCredential.provider == "deepseek",
        )
        .first()
    )
    credential = own or platform
    if credential is None:
        return None
    try:
        return decrypt_secret(credential.ciphertext)
    except Exception:
        log.exception("cannot decrypt provider credential for user %s", user_id)
        return None


def _env(instance: Instance, db: Session) -> dict[str, str]:
    domain = str(get_setting(db, "base_domain", settings.base_domain))
    env = {
        "DSH_HOME": "/data/dsh",
        "DSH_BIND_HOST": "auto",
        "DSH_BIND_PORT": "3080",
        "DSH_TRUSTED_HOSTS": f"{instance.subdomain}.{domain}",
        "DSH_PERMISSION_MODE": "workspace-write",
        "DSH_TELEMETRY_DISABLED": "1",
        "DSH_PLUGIN_ROOT": "/opt/dsh-plugins",
        "TZ": "Asia/Shanghai",
        "NO_PROXY": "127.0.0.1,localhost,172.16.0.0/12",
        "no_proxy": "127.0.0.1,localhost,172.16.0.0/12",
    }
    key = _effective_key(db, instance.user_id)
    if key:
        env["DEEPSEEK_API_KEY"] = key
    return env


def host_path(path: str) -> str:
    """Map a path inside the API container to the same file on the Docker host."""
    root = settings.data_root.rstrip("/")
    host = (settings.host_data_root or settings.data_root).rstrip("/")
    if path == root:
        return host
    if path.startswith(root + "/"):
        return host + path[len(root) :]
    return path


def _spec(instance: Instance, image: DshImage, db: Session) -> ContainerSpec:
    return ContainerSpec(
        name=instance.container_name,
        image=image.image_ref,
        env=_env(instance, db),
        mounts=[
            (host_path(instance.data_path), "/data/dsh", False),
            (host_path(instance.workspace_path), "/workspaces", False),
        ],
        cpu_cores=float(instance.cpu_cores),
        memory_mb=instance.memory_mb,
        labels={
            "dsh.console.managed": "true",
            "dsh.console.instance_id": str(instance.id),
            "dsh.console.user_id": str(instance.user_id),
            "dsh.console.version": instance.dsh_version,
        },
        network=settings.instance_network,
        shm_mb=settings.instance_shm_mb,
        pids_limit=settings.instance_pids_limit,
        user=f"{settings.instance_uid}:{settings.instance_gid}",
    )


def resolve_token(instance: Instance, force: bool = False) -> str | None:
    redis = get_redis()
    key = f"dsh:token:{instance.id}"
    if not force:
        cached = redis.get(key)
        if cached:
            return cached
    if not instance.container_id:
        return None
    try:
        logs = get_driver().logs(instance.container_id, tail=1000, since=instance.last_started_at)
    except DriverError:
        return None
    matches = TOKEN_RE.findall(logs)
    if not matches:
        return None
    token = matches[-1]
    redis.set(key, token, ex=7 * 86400)
    return token


def clear_token(instance_id: int) -> None:
    try:
        get_redis().delete(f"dsh:token:{instance_id}")
    except Exception:
        pass


def _lock(instance_id: int):
    return get_redis().set(f"lock:instance:{instance_id}", "1", nx=True, ex=300)


def _unlock(instance_id: int) -> None:
    try:
        get_redis().delete(f"lock:instance:{instance_id}")
    except Exception:
        pass


def create_instance(
    db: Session,
    user: User,
    *,
    image: DshImage,
    idle_timeout_seconds: int | None,
    actor_id: int | None = None,
) -> Instance:
    if get_instance(db, user.id) is not None:
        raise ApiError("INSTANCE_EXISTS", "你已经有实例了", 409)
    if image.status != ImageStatus.ready:
        raise ApiError("IMAGE_DISABLED", "该版本当前不可用", 409)

    quota = ensure_quota(db, user)
    plan = db.get(Plan, quota.plan_id) if quota.plan_id else user.plan
    spec = effective(quota, plan)
    capacity = probe_capacity(db)
    admit(capacity, spec["cpu_cores"], spec["memory_mb"], spec["disk_gb"])

    root = os.path.join(settings.data_root, "instances", str(user.id))
    instance = Instance(
        user_id=user.id,
        container_name=f"dsh-u-{user.id}",
        image_id=image.id,
        dsh_version=image.version,
        status=InstanceStatus.provisioning,
        subdomain=user.subdomain,
        cpu_cores=spec["cpu_cores"],
        memory_mb=spec["memory_mb"],
        disk_gb=spec["disk_gb"],
        data_path=os.path.join(root, "data"),
        workspace_path=os.path.join(root, "workspace"),
        wake_key=new_token(32),
        idle_timeout_seconds=(
            idle_timeout_seconds
            if idle_timeout_seconds is not None
            else int(get_setting(db, "default_idle_minutes", settings.default_idle_minutes)) * 60
        ),
    )
    db.add(instance)
    db.flush()
    _ensure_dirs(instance)
    log_event(db, instance, "create", None, InstanceStatus.provisioning, actor_id=actor_id)
    db.commit()
    db.refresh(instance)
    return instance


def provision(instance_id: int) -> None:
    """Create/start the container and publish the route. Runs outside the request."""
    from ..db import SessionLocal

    if not _lock(instance_id):
        return
    db = SessionLocal()
    try:
        instance = db.get(Instance, instance_id)
        if instance is None or instance.status == InstanceStatus.deleted:
            return
        driver = get_driver()
        driver.ensure_network(settings.instance_network)
        image = db.get(DshImage, instance.image_id)
        if image is None:
            raise RuntimeError("image record disappeared")
        _ensure_dirs(instance)
        if not instance.container_id:
            instance.container_id = driver.create(_spec(instance, image, db))
            db.commit()
        driver.start(instance.container_id)
        info = driver.wait_healthy(instance.container_id, timeout=90)
        if info.status in {"exited", "dead"} or info.health not in {None, "healthy"}:
            raise RuntimeError(f"container did not become healthy (status={info.status})")
        previous = instance.status
        instance.status = InstanceStatus.running
        instance.health = "healthy"
        instance.error = None
        instance.status_reason = None
        instance.last_started_at = info.started_at or utcnow()
        log_event(db, instance, "start", previous, InstanceStatus.running)
        db.commit()
        resolve_token(instance, force=True)
        _apply_caddy(db)
    except Exception as exc:
        log.exception("provision failed for instance %s", instance_id)
        db.rollback()
        instance = db.get(Instance, instance_id)
        if instance is not None:
            previous = instance.status
            instance.status = InstanceStatus.error
            instance.health = "unhealthy"
            instance.error = str(exc)[:500]
            log_event(db, instance, "provision_failed", previous, InstanceStatus.error, str(exc))
            db.commit()
    finally:
        db.close()
        _unlock(instance_id)


def start_instance(instance_id: int, actor_id: int | None = None) -> None:
    from ..db import SessionLocal

    if not _lock(instance_id):
        return
    db = SessionLocal()
    try:
        instance = db.get(Instance, instance_id)
        if instance is None:
            return
        previous = instance.status
        instance.status = InstanceStatus.starting
        instance.status_reason = None
        db.commit()
        log_event(
            db, instance, "start_requested", previous, InstanceStatus.starting, actor_id=actor_id
        )
        db.commit()

        driver = get_driver()
        driver.ensure_network(settings.instance_network)
        if instance.container_id:
            try:
                driver.inspect(instance.container_id)
            except DriverError:
                instance.container_id = None
                db.commit()
        if not instance.container_id:
            image = db.get(DshImage, instance.image_id)
            if image is None:
                raise RuntimeError("image record disappeared")
            instance.container_id = driver.create(_spec(instance, image, db))
            db.commit()
        driver.start(instance.container_id)
        info = driver.wait_healthy(instance.container_id)
        if info.status in {"exited", "dead"}:
            raise RuntimeError("container exited during startup")
        instance.status = InstanceStatus.running
        instance.health = "healthy"
        instance.error = None
        instance.last_started_at = info.started_at or utcnow()
        log_event(db, instance, "start", InstanceStatus.starting, InstanceStatus.running)
        db.commit()
        resolve_token(instance, force=True)
        _apply_caddy(db)
    except Exception as exc:
        log.exception("start failed for instance %s", instance_id)
        db.rollback()
        instance = db.get(Instance, instance_id)
        if instance is not None:
            instance.status = InstanceStatus.error
            instance.error = str(exc)[:500]
            instance.health = "unhealthy"
            log_event(
                db,
                instance,
                "start_failed",
                InstanceStatus.starting,
                InstanceStatus.error,
                str(exc),
            )
            db.commit()
    finally:
        db.close()
        _unlock(instance_id)


def stop_instance(instance_id: int, *, sleep: bool = False, actor_id: int | None = None) -> None:
    from ..db import SessionLocal

    if not _lock(instance_id):
        return
    db = SessionLocal()
    try:
        instance = db.get(Instance, instance_id)
        if instance is None:
            return
        previous = instance.status
        instance.status = InstanceStatus.stopping
        db.commit()
        log_event(
            db, instance, "stop_requested", previous, InstanceStatus.stopping, actor_id=actor_id
        )
        db.commit()
        if instance.container_id:
            get_driver().stop(instance.container_id, timeout=15)
        clear_token(instance.id)
        instance.status = InstanceStatus.sleeping if sleep else InstanceStatus.stopped
        instance.health = "unknown"
        instance.last_stopped_at = utcnow()
        log_event(
            db, instance, "sleep" if sleep else "stop", InstanceStatus.stopping, instance.status
        )
        db.commit()
        _apply_caddy(db)
    except Exception as exc:
        log.exception("stop failed for instance %s", instance_id)
        db.rollback()
        instance = db.get(Instance, instance_id)
        if instance is not None:
            instance.status = InstanceStatus.error
            instance.error = str(exc)[:500]
            log_event(
                db, instance, "stop_failed", InstanceStatus.stopping, InstanceStatus.error, str(exc)
            )
            db.commit()
    finally:
        db.close()
        _unlock(instance_id)


def restart_instance(instance_id: int, actor_id: int | None = None) -> None:
    from ..db import SessionLocal

    if not _lock(instance_id):
        return
    db = SessionLocal()
    try:
        instance = db.get(Instance, instance_id)
        if instance is None:
            return
        previous = instance.status
        instance.status = InstanceStatus.starting
        db.commit()
        log_event(db, instance, "restart", previous, InstanceStatus.starting, actor_id=actor_id)
        db.commit()
        driver = get_driver()
        if instance.container_id:
            driver.stop(instance.container_id)
            driver.start(instance.container_id)
        else:
            image = db.get(DshImage, instance.image_id)
            if image is None:
                raise RuntimeError("image record disappeared")
            instance.container_id = driver.create(_spec(instance, image, db))
            db.commit()
            driver.start(instance.container_id)
        info = driver.wait_healthy(instance.container_id)
        instance.status = InstanceStatus.running
        instance.health = "healthy"
        instance.error = None
        instance.last_started_at = info.started_at or utcnow()
        clear_token(instance.id)
        log_event(db, instance, "restart_done", InstanceStatus.starting, InstanceStatus.running)
        db.commit()
        resolve_token(instance, force=True)
        _apply_caddy(db)
    except Exception as exc:
        log.exception("restart failed for instance %s", instance_id)
        db.rollback()
        instance = db.get(Instance, instance_id)
        if instance is not None:
            instance.status = InstanceStatus.error
            instance.error = str(exc)[:500]
            db.commit()
    finally:
        db.close()
        _unlock(instance_id)


def delete_instance(db: Session, instance: Instance, actor_id: int | None = None) -> None:
    container_id = instance.container_id
    instance.status = InstanceStatus.deleting
    db.commit()
    if container_id:
        try:
            get_driver().remove(container_id)
        except DriverError:
            log.exception("failed to remove container for instance %s", instance.id)
    clear_token(instance.id)
    # M2: keep the data dir, rename it so a new instance can be created later.
    trash = os.path.join(
        settings.data_root, "trash", f"u{instance.user_id}-{int(utcnow().timestamp())}"
    )
    try:
        root = os.path.dirname(instance.data_path)
        if os.path.isdir(root):
            os.makedirs(os.path.dirname(trash), exist_ok=True)
            shutil.move(root, trash)
    except OSError:
        log.exception("failed to archive data dir for user %s", instance.user_id)
    db.delete(instance)
    db.commit()
    _apply_caddy(db)


def _apply_caddy(db: Session) -> None:
    try:
        caddy.apply(db)
    except caddy.CaddyError as exc:
        log.warning("caddy route update deferred: %s", exc)


def instance_view(instance: Instance, db: Session) -> dict:
    return {
        "id": instance.id,
        "status": instance.status.value,
        "status_reason": instance.status_reason,
        "error": instance.error,
        "health": instance.health,
        "dsh_version": instance.dsh_version,
        "image_id": instance.image_id,
        "subdomain": instance.subdomain,
        "url": instance_url(instance, db),
        "spec": {
            "cpu_cores": float(instance.cpu_cores),
            "memory_mb": instance.memory_mb,
            "disk_gb": instance.disk_gb,
        },
        "idle_timeout_seconds": instance.idle_timeout_seconds,
        "last_active_at": instance.last_active_at.isoformat() + "Z"
        if instance.last_active_at
        else None,
        "last_started_at": instance.last_started_at.isoformat() + "Z"
        if instance.last_started_at
        else None,
        "last_stopped_at": instance.last_stopped_at.isoformat() + "Z"
        if instance.last_stopped_at
        else None,
        "created_at": instance.created_at.isoformat() + "Z",
        "updated_at": instance.updated_at.isoformat() + "Z",
    }


def available_images(db: Session) -> list[DshImage]:
    return (
        db.query(DshImage)
        .filter(DshImage.status == ImageStatus.ready)
        .order_by(DshImage.is_default.desc(), DshImage.id.desc())
        .all()
    )


def default_image(db: Session) -> DshImage | None:
    return (
        db.query(DshImage)
        .filter(DshImage.status == ImageStatus.ready)
        .order_by(DshImage.is_default.desc(), DshImage.id.desc())
        .first()
    )


def touch(instance: Instance) -> None:
    instance.last_active_at = datetime.now(UTC).replace(tzinfo=None)
