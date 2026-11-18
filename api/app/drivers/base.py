from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass
class ContainerSpec:
    name: str
    image: str
    env: dict[str, str]
    mounts: list[tuple[str, str, bool]] = field(default_factory=list)
    cpu_cores: float = 1.0
    memory_mb: int = 2048
    labels: dict[str, str] = field(default_factory=dict)
    network: str = "dsh-edge"
    shm_mb: int = 512
    pids_limit: int = 4096
    user: str = "1000:1000"
    working_dir: str = "/workspaces"


@dataclass
class ContainerInfo:
    id: str
    name: str
    status: str
    health: str | None = None
    started_at: datetime | None = None
    exit_code: int | None = None
    labels: dict[str, str] = field(default_factory=dict)


@dataclass
class NodeInfo:
    cpu_cores: int
    mem_total_bytes: int
    containers_running: int
    docker_version: str


class DriverError(RuntimeError):
    """The runtime rejected an operation (image missing, daemon unreachable...)."""


class NodeDriver(Protocol):
    """The subset of the Docker API the control plane actually needs.

    Keeping this narrow matters: the API has docker.sock (root-equivalent), so
    every new method is a new way to escape the intended container shape.
    """

    def ensure_network(self, name: str) -> None: ...

    def info(self) -> NodeInfo: ...

    def image_info(self, ref: str) -> dict: ...

    def create(self, spec: ContainerSpec) -> str: ...

    def start(self, container_id: str) -> None: ...

    def stop(self, container_id: str, timeout: int = 15) -> None: ...

    def remove(self, container_id: str, force: bool = True) -> None: ...

    def inspect(self, container_id: str) -> ContainerInfo: ...

    def wait_healthy(self, container_id: str, timeout: int = 90) -> ContainerInfo: ...

    def logs(self, container_id: str, tail: int = 500, since: datetime | None = None) -> str: ...

    def stats(self, container_id: str) -> dict: ...

    def commit(self, container_id: str, image_ref: str, changes: list[str]) -> str: ...

    def pull(self, image_ref: str) -> None: ...

    def read_image_plugins(self, image_ref: str) -> dict[str, str]:
        """Map `/opt/dsh-plugins/<dir>` -> package name, without starting the workload.

        Used to refuse a platform plugin whose name the image already ships: the
        entrypoint would scan two directories claiming the same package and the
        winner would depend on directory order.
        """
        ...

    def load_image(self, path: str) -> list[str]:
        """`docker load` a `docker save` tarball; returns the loaded references."""
        ...

    def open_exec(
        self, container_id: str, cmd: list[str], *, tty: bool = True
    ) -> tuple[str, object]:
        """Start an interactive exec; returns (exec_id, raw socket)."""
        ...

    def resize_exec(self, exec_id: str, rows: int, cols: int) -> None:
        """Propagate a terminal resize (SIGWINCH) to the exec'd TTY."""
        ...
