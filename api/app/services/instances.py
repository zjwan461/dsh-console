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
    DshImage,
    ImageStatus,
    Instance,
    InstanceEvent,
    InstanceStatus,
    Plan,
    User,
)
from ..redis import get_redis
from ..security import new_token, utcnow
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
    domain = str(get_setting(db, "base_domain", settings.base_domain))
    return f"{settings.public_scheme}://{instance.subdomain}.{domain}{public_authority(domain)}"


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


def _failure_detail(driver, container_id: str | None) -> str:
    """The last meaningful log line of a container that refused to start."""
    if not container_id:
        return ""
    try:
        tail = driver.logs(container_id, tail=30)
    except Exception:  # noqa: BLE001 - diagnostics must never mask the real error
        return ""
    lines = [line.strip() for line in tail.splitlines() if line.strip()]
    if not lines:
        return ""
    # Prefer an explicit error; otherwise the last line still beats a bare status.
    for line in reversed(lines):
        if "ERROR" in line or "error" in line:
            return f": {line[:280]}"
    return f": {lines[-1][:280]}"


def _ensure_dirs(instance: Instance) -> None:
    for path in (instance.data_path, instance.workspace_path):
        os.makedirs(path, mode=0o700, exist_ok=True)
        if hasattr(os, "chown"):
            try:
                os.chown(path, settings.instance_uid, settings.instance_gid)
            except PermissionError:  # dev on Windows / non-root
                pass


def _effective_credential(db: Session, user_id: int) -> tuple[str | None, str | None, str | None]:
    from . import provider

    return provider.effective(db, user_id)


def public_authority(domain: str) -> str:
    """`host:port` exactly as the browser will send it, or just `host`."""
    port = settings.public_port
    if not port:
        return ""
    if settings.public_scheme == "https" and port == 443:
        return ""
    if settings.public_scheme == "http" and port == 80:
        return ""
    return f":{port}"


def _env(instance: Instance, db: Session) -> dict[str, str]:
    domain = str(get_setting(db, "base_domain", settings.base_domain))
    # DSH matches the authority (host:port) it was started with, so a console on
    # a non-standard port has to register the port-bearing form.
    host = f"{instance.subdomain}.{domain}"
    env = {
        "DSH_HOME": "/data/dsh",
        "DSH_BIND_HOST": "auto",
        "DSH_BIND_PORT": "3080",
        "DSH_TRUSTED_HOSTS": f"{host},{host}{public_authority(domain)}",
        "DSH_PERMISSION_MODE": "workspace-write",
        "DSH_TELEMETRY_DISABLED": "1",
        "DSH_PLUGIN_ROOT": "/opt/dsh-plugins",
        "TZ": "Asia/Shanghai",
        "NO_PROXY": "127.0.0.1,localhost,172.16.0.0/12",
        "no_proxy": "127.0.0.1,localhost,172.16.0.0/12",
    }
    key, _, _ = _effective_credential(db, instance.user_id)
    if key:
        env["DEEPSEEK_API_KEY"] = key
    return env


# Provider id we own inside the instance's settings.yaml. Prefixed so it cannot
# collide with something the user configured in the DSH web UI.
CONSOLE_PROVIDER_ID = "console-gateway"


def _write_model_settings(instance: Instance, db: Session) -> None:
    """Write the user's endpoint (and optional model) into `settings.yaml`.

    Two shapes, matching what DSH itself documents:

    * endpoint only  -> `llm-deepseek.baseURL` (DeepSeek-compatible gateway,
                        model catalogue unchanged)
    * endpoint+model -> a generic `llm-pi-ai` provider (openai-completions) plus
                        `agent-default-model`, so a gateway exposing qwen/gpt
                        models actually works

    The file is merged, never replaced: users may have configured other providers
    from the DSH web UI, and an unparseable file is left untouched.
    """
    import tempfile

    import yaml

    _, base_url, model = _effective_credential(db, instance.user_id)
    path = os.path.join(instance.data_path, "settings.yaml")
    exists = os.path.exists(path)
    if not base_url and not model and not exists:
        return

    data: dict = {}
    if exists:
        try:
            with open(path, encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh)
            data = loaded if isinstance(loaded, dict) else {}
        except (OSError, yaml.YAMLError) as exc:
            log.warning("cannot read %s, leaving it untouched: %s", path, exc)
            return

    deepseek = data.get("llm-deepseek")
    if not isinstance(deepseek, dict):
        deepseek = {}
    pi = data.get("llm-pi-ai")
    if not isinstance(pi, dict):
        pi = {}
    providers = pi.get("providers")
    if not isinstance(providers, dict):
        providers = {}

    if base_url:
        # Keep the DeepSeek adapter pointed at the same gateway as well.
        deepseek["baseURL"] = base_url
    else:
        deepseek.pop("baseURL", None)

    if base_url and model:
        providers[CONSOLE_PROVIDER_ID] = {
            "displayName": "Console gateway",
            "api": "openai-completions",
            "baseURL": base_url,
            "apiKeyEnv": "DEEPSEEK_API_KEY",
            "models": [
                {
                    "id": model,
                    "name": model,
                    # Conservative defaults; users who need exact limits can edit
                    # settings.yaml in the DSH UI.
                    "contextWindow": 128000,
                    "maxTokens": 8192,
                }
            ],
        }
        data["agent-default-model"] = {"provider": CONSOLE_PROVIDER_ID, "model": model}
    else:
        if providers.pop(CONSOLE_PROVIDER_ID, None) is not None and not providers:
            pi.pop("providers", None)
        default = data.get("agent-default-model")
        if isinstance(default, dict) and default.get("provider") == CONSOLE_PROVIDER_ID:
            data.pop("agent-default-model", None)

    if providers:
        pi["providers"] = providers
        data["llm-pi-ai"] = pi
    elif not pi:
        data.pop("llm-pi-ai", None)
    else:
        data["llm-pi-ai"] = pi

    if deepseek:
        data["llm-deepseek"] = deepseek
    else:
        data.pop("llm-deepseek", None)

    if not data and not base_url and not model:
        return

    os.makedirs(instance.data_path, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=instance.data_path, prefix=".settings-", suffix=".yaml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)
        if hasattr(os, "chown"):
            try:
                os.chown(tmp, settings.instance_uid, settings.instance_gid)
            except PermissionError:
                pass
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _config_hash(spec: ContainerSpec, base_url: str | None = None, model: str | None = None) -> str:
    """Identity of everything that requires re-creating (not just restarting) a container."""
    import hashlib
    import json

    payload = {
        "image": spec.image,
        "env": dict(sorted(spec.env.items())),
        "mounts": spec.mounts,
        "cpu": spec.cpu_cores,
        "mem": spec.memory_mb,
        "shm": spec.shm_mb,
        "pids": spec.pids_limit,
        "user": spec.user,
        # The endpoint/model live in settings.yaml rather than the env. They are
        # hashed so one restart applies key + endpoint + model consistently,
        # instead of depending on whether DSH happened to hot-reload the file.
        "base_url": base_url or "",
        "model": model or "",
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def _needs_recreate(instance: Instance, spec: ContainerSpec, driver) -> bool:
    """True when the running container predates a config change (key, image...)."""
    if not instance.container_id:
        return True
    try:
        info = driver.inspect(instance.container_id)
    except DriverError:
        return True
    expected = spec.labels.get("dsh.console.config_hash")
    return info.labels.get("dsh.console.config_hash") != expected


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
    image_ref = image.image_ref
    if instance.user_image_id:
        from ..models import UserImage

        user_image = db.get(UserImage, instance.user_image_id)
        if user_image is not None and user_image.status.value == "ready":
            image_ref = user_image.image_ref
    from . import plugins as plugin_repo

    platform_plugin_mounts = [
        (host_path(str(host)), target, True)
        for host, target, _ro in plugin_repo.plugin_mounts(db, instance)
    ]
    spec = ContainerSpec(
        name=instance.container_name,
        image=image_ref,
        env=_env(instance, db),
        mounts=[
            (host_path(instance.data_path), "/data/dsh", False),
            (host_path(instance.workspace_path), "/workspaces", False),
            *platform_plugin_mounts,
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
    # Lets a later start/restart detect that the *effective* config changed (key,
    # base URL, image...) and re-create the container instead of reusing stale env.
    _, base_url, model = _effective_credential(db, instance.user_id)
    spec.labels["dsh.console.config_hash"] = _config_hash(spec, base_url, model)
    return spec


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


def _lock(instance_id: int) -> bool:
    """Take the per-instance operation lock.

    Fails OPEN when Redis is unavailable: dropping a user's stop/start because the
    cache blipped is worse than occasionally running two operations, and the lock
    is a serialisation aid, not a safety boundary. (A closed failure here was
    observed to swallow stop requests silently.)
    """
    try:
        return bool(get_redis().set(f"lock:instance:{instance_id}", "1", nx=True, ex=300))
    except Exception:
        log.warning("instance lock unavailable; proceeding unlocked", exc_info=True)
        return True


def _unlock(instance_id: int) -> None:
    try:
        get_redis().delete(f"lock:instance:{instance_id}")
    except Exception:
        pass


def acquire_lock(instance_id: int, status: str) -> None:
    """Lock from the request handler so a busy instance yields 409, not a no-op."""
    if not _lock(instance_id):
        raise ApiError(
            "INSTANCE_BUSY",
            "实例正在执行其他操作，请稍后重试",
            409,
            {"status": status},
        )


def _try_lock(instance_id: int) -> bool:
    return _lock(instance_id)


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


def provision(instance_id: int, *, prelocked: bool = False) -> None:
    """Create/start the container and publish the route. Runs outside the request."""
    from ..db import SessionLocal

    if not prelocked and not _lock(instance_id):
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
        _write_model_settings(instance, db)
        from . import plugins as plugin_repo
        from . import skills as skill_repo

        skill_repo.sync_instance_skills(db, instance)
        plugin_repo.sync_instance_plugins(db, instance)
        spec = _spec(instance, image, db)
        if _needs_recreate(instance, spec, driver):
            if instance.container_id:
                try:
                    driver.remove(instance.container_id, force=True)
                except DriverError:
                    log.warning("could not remove stale container %s", instance.container_id)
                instance.container_id = None
            instance.container_id = driver.create(spec)
            db.commit()
        driver.start(instance.container_id)
        info = driver.wait_healthy(instance.container_id, timeout=settings.instance_health_timeout)
        if info.status in {"exited", "dead"} or info.health not in {None, "healthy"}:
            # The container's own explanation is far more useful than the status:
            # e.g. "[dsh] ERROR: $DSH_HOME (/data/dsh) is not writable by uid
            # 1000:1000; it is owned by 0:0" points straight at a host permission
            # problem the operator can fix in one command.
            raise RuntimeError(
                f"container did not become healthy (status={info.status})"
                f"{_failure_detail(driver, instance.container_id)}"
            )
        previous = instance.status
        instance.status = InstanceStatus.running
        instance.needs_restart = False
        plugin_repo.cleanup_stale_plugin_dirs(db, instance)
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


def start_instance(
    instance_id: int, actor_id: int | None = None, *, prelocked: bool = False
) -> None:
    from ..db import SessionLocal

    if not prelocked and not _lock(instance_id):
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
        image = db.get(DshImage, instance.image_id)
        if image is None:
            raise RuntimeError("image record disappeared")
        _ensure_dirs(instance)
        _write_model_settings(instance, db)
        from . import plugins as plugin_repo
        from . import skills as skill_repo

        skill_repo.sync_instance_skills(db, instance)
        plugin_repo.sync_instance_plugins(db, instance)
        spec = _spec(instance, image, db)
        if _needs_recreate(instance, spec, driver):
            if instance.container_id:
                try:
                    driver.remove(instance.container_id, force=True)
                except DriverError:
                    log.warning("could not remove stale container %s", instance.container_id)
                instance.container_id = None
                db.commit()
            instance.container_id = driver.create(spec)
            db.commit()
        driver.start(instance.container_id)
        info = driver.wait_healthy(instance.container_id, timeout=settings.instance_health_timeout)
        if info.status in {"exited", "dead"}:
            raise RuntimeError("container exited during startup")
        instance.status = InstanceStatus.running
        instance.needs_restart = False
        plugin_repo.cleanup_stale_plugin_dirs(db, instance)
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


def stop_instance(
    instance_id: int,
    *,
    sleep: bool = False,
    actor_id: int | None = None,
    reason: str | None = None,
    prelocked: bool = False,
) -> None:
    from ..db import SessionLocal

    if not prelocked and not _lock(instance_id):
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
        instance.status_reason = reason
        instance.last_stopped_at = utcnow()
        log_event(
            db,
            instance,
            "sleep" if sleep else "stop",
            InstanceStatus.stopping,
            instance.status,
            reason or "",
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


def apply_plan(
    db: Session, instance: Instance, *, cpu_cores: float, memory_mb: int, disk_gb: int | None
) -> bool:
    """Copy a new plan's shape onto the instance and force a container rebuild.

    Docker cannot change `--cpus`/`--memory` on a live container, so the stored
    values alone would lie about what is actually running. The instance spec is
    updated and the container is re-created (its config hash changes), which is
    the same mechanism a version switch uses.
    """
    changed = (
        float(instance.cpu_cores) != float(cpu_cores)
        or instance.memory_mb != memory_mb
        or (disk_gb is not None and instance.disk_gb != disk_gb)
    )
    if not changed:
        return False
    instance.cpu_cores = cpu_cores
    instance.memory_mb = memory_mb
    if disk_gb is not None:
        instance.disk_gb = disk_gb
    db.commit()
    log_event(db, instance, "plan_applied", None, None, f"{cpu_cores}C / {memory_mb}MB")
    db.commit()
    return True


def restart_instance(
    instance_id: int, actor_id: int | None = None, *, prelocked: bool = False
) -> None:
    from ..db import SessionLocal

    if not prelocked and not _lock(instance_id):
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
        image = db.get(DshImage, instance.image_id)
        if image is None:
            raise RuntimeError("image record disappeared")
        _ensure_dirs(instance)
        # settings.yaml (endpoint) and the env (key) are read at container start;
        # a changed config means the old container can no longer be reused.
        _write_model_settings(instance, db)
        from . import plugins as plugin_repo
        from . import skills as skill_repo

        skill_repo.sync_instance_skills(db, instance)
        plugin_repo.sync_instance_plugins(db, instance)
        spec = _spec(instance, image, db)
        if _needs_recreate(instance, spec, driver):
            if instance.container_id:
                try:
                    driver.stop(instance.container_id)
                except DriverError:
                    pass
                try:
                    driver.remove(instance.container_id, force=True)
                except DriverError:
                    log.warning("could not remove stale container %s", instance.container_id)
            instance.container_id = driver.create(spec)
            db.commit()
            driver.start(instance.container_id)
        else:
            driver.stop(instance.container_id)
            driver.start(instance.container_id)
        info = driver.wait_healthy(instance.container_id, timeout=settings.instance_health_timeout)
        instance.status = InstanceStatus.running
        instance.needs_restart = False
        plugin_repo.cleanup_stale_plugin_dirs(db, instance)
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
        "needs_restart": bool(instance.needs_restart),
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
