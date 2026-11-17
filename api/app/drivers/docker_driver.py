from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from datetime import UTC, datetime

from ..config import settings
from .base import ContainerInfo, ContainerSpec, DriverError, NodeInfo

log = logging.getLogger("dsh.docker")

HEALTH_CMD = (
    "curl --noproxy '*' -s -o /dev/null -w '%{http_code}' "
    '"http://$(cat /data/dsh/.runtime/bind-host 2>/dev/null || echo 127.0.0.1):3080/" '
    "| grep -qE '^(200|401)$'"
)


class DockerDriver:
    def __init__(self) -> None:
        self._client = None

    @property
    def client(self):
        if self._client is None:
            try:
                import docker
            except ImportError as exc:  # pragma: no cover
                raise DriverError("docker SDK is not installed") from exc
            try:
                self._client = docker.DockerClient(
                    base_url=settings.docker_host,
                    version="auto",
                    timeout=settings.docker_timeout,
                )
            except Exception as exc:  # pragma: no cover - depends on the host
                raise DriverError(f"cannot connect to Docker: {exc}") from exc
        return self._client

    @contextmanager
    def _long_op(self, seconds: int):
        """Temporarily raise the HTTP timeout for slow daemon calls.

        Image commit and pull routinely exceed the default request timeout: a
        28-layer, ~2 GB image took longer than 30s to commit, which surfaced as
        `ReadTimeout` (and, on retry, as a confusing "content digest not found").
        """
        api = self.client.api
        original = api.timeout
        api.timeout = seconds
        try:
            yield
        finally:
            api.timeout = original

    def ensure_network(self, name: str) -> None:
        import docker

        try:
            self.client.networks.get(name)
        except docker.errors.NotFound:
            self.client.networks.create(name, driver="bridge")
            log.info("created docker network %s", name)

    def info(self) -> NodeInfo:
        data = self.client.info()
        return NodeInfo(
            cpu_cores=int(data.get("NCPU") or 1),
            mem_total_bytes=int(data.get("MemTotal") or 0),
            containers_running=int(data.get("ContainersRunning") or 0),
            docker_version=str(data.get("ServerVersion") or "unknown"),
        )

    def image_info(self, ref: str) -> dict:
        import docker

        try:
            image = self.client.images.get(ref)
        except docker.errors.ImageNotFound as exc:
            raise DriverError(f"image {ref} is not present on this host") from exc
        attrs = image.attrs
        repo_digests = attrs.get("RepoDigests") or []
        digest = repo_digests[0].split("@", 1)[-1] if repo_digests else None
        return {
            "id": image.id,
            "digest": digest,
            "size": int(attrs.get("Size") or 0),
            "arch": (attrs.get("Architecture") or "amd64"),
            "labels": (attrs.get("Config") or {}).get("Labels") or {},
        }

    def create(self, spec: ContainerSpec) -> str:
        volumes = {
            source: {"bind": target, "mode": "ro" if read_only else "rw"}
            for source, target, read_only in spec.mounts
        }
        kwargs = {
            "image": spec.image,
            "name": spec.name,
            "detach": True,
            "environment": spec.env,
            "volumes": volumes,
            "working_dir": spec.working_dir,
            "user": spec.user,
            "labels": spec.labels,
            "network": spec.network,
            "nano_cpus": int(spec.cpu_cores * 1_000_000_000),
            "mem_limit": f"{spec.memory_mb}m",
            "memswap_limit": f"{spec.memory_mb}m",
            "pids_limit": spec.pids_limit,
            "shm_size": f"{spec.shm_mb}m",
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "restart_policy": {"Name": "no"},
            "healthcheck": {
                "test": ["CMD-SHELL", HEALTH_CMD],
                "interval": 30_000_000_000,
                "timeout": 5_000_000_000,
                "retries": 5,
                "start_period": 90_000_000_000,
            },
        }
        try:
            container = self.client.containers.create(**kwargs)
        except Exception as exc:
            raise DriverError(f"docker create failed: {exc}") from exc
        log.info("created container %s (%s)", spec.name, container.id[:12])
        return container.id

    def start(self, container_id: str) -> None:
        try:
            self.client.containers.get(container_id).start()
        except Exception as exc:
            raise DriverError(f"docker start failed: {exc}") from exc

    def stop(self, container_id: str, timeout: int = 15) -> None:
        try:
            container = self.client.containers.get(container_id)
            container.stop(timeout=timeout)
        except Exception as exc:
            # Already stopped/removed is not an error for reconciliation.
            if "is not running" in str(exc).lower() or "no such container" in str(exc).lower():
                return
            raise DriverError(f"docker stop failed: {exc}") from exc

    def remove(self, container_id: str, force: bool = True) -> None:
        try:
            self.client.containers.get(container_id).remove(force=force)
        except Exception as exc:
            if "no such container" in str(exc).lower():
                return
            raise DriverError(f"docker remove failed: {exc}") from exc

    def inspect(self, container_id: str) -> ContainerInfo:
        try:
            container = self.client.containers.get(container_id)
            attrs = container.attrs
        except Exception as exc:
            raise DriverError(f"docker inspect failed: {exc}") from exc
        state = attrs.get("State") or {}
        health = (state.get("Health") or {}).get("Status")
        started_raw = state.get("StartedAt")
        started_at = None
        if started_raw and not started_raw.startswith("0001-"):
            try:
                started_at = datetime.fromisoformat(started_raw.replace("Z", "+00:00"))
            except ValueError:
                started_at = None
        return ContainerInfo(
            id=container.id,
            name=(attrs.get("Name") or "").lstrip("/"),
            status=str(state.get("Status") or "unknown"),
            health=health,
            started_at=started_at,
            exit_code=state.get("ExitCode"),
        )

    def wait_healthy(self, container_id: str, timeout: int = 90) -> ContainerInfo:
        deadline = time.monotonic() + timeout
        last = self.inspect(container_id)
        while time.monotonic() < deadline:
            last = self.inspect(container_id)
            if last.status in {"exited", "dead"}:
                return last
            if last.health == "healthy":
                return last
            if last.health is None and last.status == "running":
                # No healthcheck configured: running is the best signal we have.
                return last
            time.sleep(2)
        return last

    def logs(self, container_id: str, tail: int = 500, since: datetime | None = None) -> str:
        container = self.client.containers.get(container_id)
        kwargs: dict = {"tail": tail, "stdout": True, "stderr": True}
        if since is not None:
            if since.tzinfo is None:
                since = since.replace(tzinfo=UTC)
            kwargs["since"] = int(since.timestamp())
        raw = container.logs(**kwargs)
        return raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)

    def stats(self, container_id: str) -> dict:
        container = self.client.containers.get(container_id)
        data = container.stats(stream=False)
        cpu = data.get("cpu_stats") or {}
        pre = data.get("precpu_stats") or {}
        cpu_delta = (cpu.get("cpu_usage", {}).get("total_usage", 0)) - (
            pre.get("cpu_usage", {}).get("total_usage", 0)
        )
        system_delta = (cpu.get("system_cpu_usage", 0)) - (pre.get("system_cpu_usage", 0))
        online = cpu.get("online_cpus") or 1
        percent = (cpu_delta / system_delta * online * 100.0) if system_delta > 0 else 0.0
        mem = data.get("memory_stats") or {}
        return {
            "cpu_percent": round(percent, 2),
            "mem_used_bytes": int(mem.get("usage") or 0),
            "mem_limit_bytes": int(mem.get("limit") or 0),
            "pids": int((data.get("pids_stats") or {}).get("current") or 0),
        }

    def commit(self, container_id: str, image_ref: str, changes: list[str]) -> str:
        repository, _, tag = image_ref.rpartition(":")
        if not repository:
            repository, tag = image_ref, "latest"
        try:
            container = self.client.containers.get(container_id)
            with self._long_op(settings.docker_commit_timeout):
                image = container.commit(repository=repository, tag=tag, changes=changes)
            return image.id
        except DriverError:
            raise
        except Exception as exc:
            raise DriverError(f"docker commit failed: {exc}") from exc

    def pull(self, image_ref: str) -> None:
        try:
            with self._long_op(settings.docker_pull_timeout):
                self.client.images.pull(image_ref)
        except Exception as exc:
            raise DriverError(f"docker pull failed: {exc}") from exc

    def load_image(self, path: str) -> list[str]:
        try:
            with open(path, "rb") as fh, self._long_op(settings.docker_pull_timeout):
                output = self.client.images.load(fh)
        except Exception as exc:
            raise DriverError(f"docker load failed: {exc}") from exc
        refs: list[str] = []
        for image in output or []:
            for tag in image.get("RepoTags") or []:
                refs.append(tag)
        return refs

    def open_exec(
        self, container_id: str, cmd: list[str], *, tty: bool = True
    ) -> tuple[str, object]:
        """Start an interactive exec and return (exec_id, raw socket).

        The low-level API is used instead of `container.exec_run` because the
        exec id is needed afterwards for `exec_resize` (SIGWINCH); without it a
        full-screen program cannot know the terminal size.
        """
        api = self.client.api
        try:
            created = api.exec_create(
                container_id,
                cmd,
                tty=tty,
                stdin=True,
                stdout=True,
                stderr=True,
            )
            exec_id = created["Id"]
            started = api.exec_start(exec_id, tty=tty, socket=True)
        except Exception as exc:
            # Older daemons / unusual proxies can reject the socket upgrade; fall
            # back to the high-level call, which still gives a working shell but
            # cannot be resized.
            log.warning("low-level exec failed, falling back: %s", exc)
            try:
                container = self.client.containers.get(container_id)
                result = container.exec_run(cmd, tty=tty, stdin=True, socket=True)
            except Exception as exc2:
                raise DriverError(f"cannot open terminal: {exc2}") from exc2
            return "", getattr(result.output, "_sock", result.output)
        # docker-py returns either a SocketIO wrapper or a bare socket depending
        # on version; normalise to something with sendall/recv/close.
        raw = getattr(started, "_sock", started)
        return exec_id, raw

    def resize_exec(self, exec_id: str, rows: int, cols: int) -> None:
        if not exec_id:
            return
        try:
            # docker-py names these height/width (they map to the API's h/w).
            self.client.api.exec_resize(exec_id, height=rows, width=cols)
        except Exception:  # noqa: BLE001 - a resize race must not kill the session
            log.debug("exec resize failed for %s", exec_id, exc_info=True)
