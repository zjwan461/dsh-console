from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.drivers import DriverError, get_driver, reset_driver
from app.services.caddy import build_config
from app.services.capacity import probe_capacity


def _login(client: TestClient, username: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password, "remember": True},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["csrf_token"]


def test_access_log_lives_on_top_level_logging(client: TestClient):
    """Caddy rejects `logs` on the server AND on the http app; the writer belongs
    in the top-level `logging.logs` map, with the server naming the logger."""
    with SessionLocal() as db:
        config = build_config(db)
    server = config["apps"]["http"]["servers"]["srv0"]
    assert server["logs"] == {"default_logger_name": "access"}
    assert "logs" not in config["apps"]["http"]
    logging_cfg = config["logging"]["logs"]
    assert logging_cfg["access"]["writer"]["output"] == "net"
    assert logging_cfg["access"]["writer"]["address"].startswith("console-worker:")
    assert "http.log.access.access" in logging_cfg["access"]["include"]
    assert logging_cfg["default"]["exclude"] == ["http.log.access.access"]


def test_console_proxies_api_and_browser_entry_points(client: TestClient):
    """The console site must forward /go and /w to the API; otherwise the SPA
    catch-all answers a launch ticket with index.html (200) instead of a 302."""
    with SessionLocal() as db:
        config = build_config(db)
    console = config["apps"]["http"]["servers"]["srv0"]["routes"][0]
    subroute = console["handle"][0]["routes"]
    paths = subroute[0]["match"][0]["path"]
    for prefix in ("/api/*", "/ws/*", "/go/*", "/w/*"):
        assert prefix in paths, paths
    assert subroute[0]["handle"][0]["handler"] == "reverse_proxy"
    # The SPA fallback must come last and must not swallow the entry points.
    assert subroute[-1]["handle"][0]["handler"] == "rewrite"


def test_route_order_and_wake_subroute(client: TestClient):
    with SessionLocal() as db:
        config = build_config(db)
    routes = config["apps"]["http"]["servers"]["srv0"]["routes"]
    hosts = [route["match"][0]["host"][0] for route in routes[:-1]]
    assert hosts[0] == "console.dsh.example.com"
    assert routes[-1]["match"][0]["host"][0].startswith("*.")


def test_public_scheme_http_skips_tls(client: TestClient):
    with SessionLocal() as db:
        config = build_config(db)
    # The test environment runs with PUBLIC_SCHEME=http.
    assert config["apps"]["http"]["servers"]["srv0"]["listen"] == [":80"]
    assert "tls" not in config["apps"]


def test_capacity_degrades_when_docker_is_down(client: TestClient, monkeypatch):
    """An unreachable runtime must degrade the dashboards, not 500 them."""

    class Broken:
        def info(self):
            raise DriverError("cannot connect to Docker")

        def ensure_network(self, name):  # pragma: no cover
            raise DriverError("cannot connect to Docker")

    monkeypatch.setattr("app.services.capacity.get_driver", lambda: Broken())
    with SessionLocal() as db:
        capacity = probe_capacity(db)
    assert capacity["docker_ok"] is False
    assert capacity["docker_error"]
    assert capacity["can_admit"] == {"cpu": False, "memory": False, "disk": False}

    # Admin overview must still render.
    client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin12345", "remember": True},
    )
    response = client.get("/api/v1/admin/metrics/overview")
    assert response.status_code == 200, response.text
    assert response.json()["data"]["capacity"]["docker_ok"] is False

    # Creating an instance must fail with a clear 503, not admit blindly.
    from app.errors import ApiError
    from app.services.capacity import admit

    with pytest.raises(ApiError) as excinfo:
        admit(capacity, 1, 1024, 10)
    assert excinfo.value.code == "DOCKER_UNAVAILABLE"
    assert excinfo.value.status_code == 503

    reset_driver()
    assert get_driver() is not None
