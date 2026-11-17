from __future__ import annotations

import secrets
import time
from datetime import UTC, datetime
from threading import Lock

from .base import ContainerInfo, ContainerSpec, DriverError, NodeInfo

_FAKE_TOKEN = "FAKE" + "A" * 39


class FakeDriver:
    """In-memory driver used by tests and by `DOCKER_FAKE=1` local development.

    It intentionally models the observable states (created/running/exited,
    healthy/unhealthy, logs containing the launch token) so the service layer is
    exercised without a Docker daemon.
    """

    containers: dict[str, dict] = {}
    networks: set[str] = set()
    fail_create = False
    fail_start = False
    cpu_cores = 8
    mem_total_bytes = 32 * 1024**3
    docker_version = "fake-27"

    def __init__(self) -> None:
        self._lock = Lock()

    def reset(self) -> None:
        with self._lock:
            self.containers.clear()
            self.networks.clear()
        FakeDriver.fail_create = False
        FakeDriver.fail_start = False

    def ensure_network(self, name: str) -> None:
        self.networks.add(name)

    def info(self) -> NodeInfo:
        return NodeInfo(
            cpu_cores=self.cpu_cores,
            mem_total_bytes=self.mem_total_bytes,
            containers_running=sum(1 for c in self.containers.values() if c["status"] == "running"),
            docker_version=self.docker_version,
        )

    def image_info(self, ref: str) -> dict:
        if not ref:
            raise DriverError("empty image ref")
        return {
            "id": "sha256:" + secrets.token_hex(32),
            "digest": "sha256:" + secrets.token_hex(32),
            "size": 2_147_483_648,
            "arch": "amd64",
            "labels": {"dsh.version": ref.rsplit(":", 1)[-1]},
        }

    def create(self, spec: ContainerSpec) -> str:
        if FakeDriver.fail_create:
            raise DriverError("fake create failure")
        container_id = secrets.token_hex(32)
        with self._lock:
            self.containers[container_id] = {
                "id": container_id,
                "name": spec.name,
                "status": "created",
                "health": "starting",
                "started_at": None,
                "spec": spec,
                "created_at": time.time(),
            }
        return container_id

    def start(self, container_id: str) -> None:
        if FakeDriver.fail_start:
            raise DriverError("fake start failure")
        item = self.containers.get(container_id)
        if item is None:
            raise DriverError("no such container")
        item["status"] = "running"
        item["health"] = "healthy"
        item["started_at"] = datetime.now(UTC)

    def stop(self, container_id: str, timeout: int = 15) -> None:
        item = self.containers.get(container_id)
        if item is None:
            return
        item["status"] = "exited"
        item["health"] = None

    def remove(self, container_id: str, force: bool = True) -> None:
        self.containers.pop(container_id, None)

    def inspect(self, container_id: str) -> ContainerInfo:
        item = self.containers.get(container_id)
        if item is None:
            raise DriverError("no such container")
        return ContainerInfo(
            id=container_id,
            name=item["name"],
            status=item["status"],
            health=item["health"],
            started_at=item["started_at"],
        )

    def wait_healthy(self, container_id: str, timeout: int = 90) -> ContainerInfo:
        item = self.containers.get(container_id)
        if item is None:
            raise DriverError("no such container")
        item["status"] = "running"
        item["health"] = "healthy"
        return self.inspect(container_id)

    def logs(self, container_id: str, tail: int = 500, since: datetime | None = None) -> str:
        item = self.containers.get(container_id)
        if item is None:
            raise DriverError("no such container")
        return (
            "dsh version: 0.2.1-rc.5\n"
            f"[dsh] listening on http://172.18.0.2:3080/?token={_FAKE_TOKEN}\n"
        )

    def stats(self, container_id: str) -> dict:
        return {
            "cpu_percent": 3.5,
            "mem_used_bytes": 512 * 1024**2,
            "mem_limit_bytes": 4 * 1024**3,
            "pids": 12,
        }
