from __future__ import annotations

from threading import Lock

from ..config import settings
from .base import NodeDriver

_lock = Lock()
_driver: NodeDriver | None = None


def get_driver() -> NodeDriver:
    global _driver
    with _lock:
        if _driver is None:
            if settings.docker_fake:
                from .fake_driver import FakeDriver

                _driver = FakeDriver()
            else:
                from .docker_driver import DockerDriver

                _driver = DockerDriver()
        return _driver


def reset_driver() -> None:
    """Test hook: drop the cached driver (and the fake's containers)."""
    global _driver
    with _lock:
        if _driver is not None and hasattr(_driver, "reset"):
            _driver.reset()  # type: ignore[attr-defined]
        _driver = None
