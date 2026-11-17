from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import settings
from app.db import SessionLocal
from app.models import Instance, User


def _login(client: TestClient, username: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password, "remember": True},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["csrf_token"]


def _login_cookie(client: TestClient, username: str, password: str) -> dict[str, str]:
    """Return the session cookie from the login response itself.

    Reading it from the client's cookie jar is ambiguous once more than one user
    has logged in during the module (the jar can hold both entries).
    """
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password, "remember": True},
    )
    assert response.status_code == 200, response.text
    raw = response.headers.get("set-cookie", "")
    assert settings.cookie_name in raw, raw
    return {"cookie": raw.split(";", 1)[0]}


@pytest.fixture(scope="module")
def terminal_instance(client: TestClient) -> int:
    """A dedicated user + instance so this module is independent of other tests."""
    admin_csrf = _login(client, "admin", "admin12345")
    client.post(
        "/api/v1/admin/images",
        json={"image_ref": "ghcr.io/you/dsh:term", "version": "term", "is_default": True},
        headers={"X-CSRF-Token": admin_csrf},
    )
    created = client.post(
        "/api/v1/admin/users",
        json={
            "username": "termuser",
            "password": "termpass123",
            "role": "user",
            "must_change_password": False,
        },
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert created.status_code in (201, 409), created.text
    user_csrf = _login(client, "termuser", "termpass123")
    response = client.post("/api/v1/instances/me", json={}, headers={"X-CSRF-Token": user_csrf})
    assert response.status_code == 202, response.text
    with SessionLocal() as db:
        user = db.query(User).filter(User.username == "termuser").one()
        instance = db.query(Instance).filter(Instance.user_id == user.id).one()
        return instance.id


def test_terminal_websocket_uses_cookie_auth(client: TestClient, terminal_instance: int):
    """The terminal is a WS route: it must not be wrapped in the HTTP CSRF guard
    (that surfaced as a 500 during the handshake) and must require admin.

    Protocol: the server announces readiness with a JSON text frame, then streams
    raw PTY bytes as binary frames; keystrokes go back as binary.
    """
    cookie = _login_cookie(client, "admin", "admin12345")
    url = f"/api/v1/admin/instances/{terminal_instance}/terminal"
    with client.websocket_connect(url, headers=cookie) as ws:
        ready = json.loads(ws.receive_text())
        assert ready["type"] == "ready"
        assert ready["shell"] == "bash"

        # FakeDriver echoes what it receives; the banner arrives first.
        banner = ws.receive_bytes()
        assert b"FakeDriver terminal" in banner

        ws.send_bytes(b"echo hi\r")
        echoed = ws.receive_bytes()
        assert b"echo hi" in echoed


def test_terminal_accepts_resize_control(client: TestClient, terminal_instance: int):
    """A resize control frame must be accepted without breaking the session."""
    cookie = _login_cookie(client, "admin", "admin12345")
    url = f"/api/v1/admin/instances/{terminal_instance}/terminal"
    with client.websocket_connect(url, headers=cookie) as ws:
        assert json.loads(ws.receive_text())["type"] == "ready"
        ws.receive_bytes()  # banner
        ws.send_text(json.dumps({"type": "resize", "cols": 120, "rows": 32}))
        ws.send_text(json.dumps({"type": "ping"}))
        assert json.loads(ws.receive_text())["type"] == "pong"
        # The session still works after a resize.
        ws.send_bytes(b"pwd\r")
        assert b"pwd" in ws.receive_bytes()


def test_terminal_rejects_non_admin(client: TestClient, terminal_instance: int):
    cookie = _login_cookie(client, "termuser", "termpass123")
    url = f"/api/v1/admin/instances/{terminal_instance}/terminal"
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(url, headers=cookie) as ws:
            ws.receive_text()


def test_terminal_is_governed_by_setting_and_audited(client: TestClient, terminal_instance: int):
    """The `admin_terminal_enabled` setting must actually gate the route, and both
    opening and closing a session must leave an audit trail."""
    from app.models import AuditLog
    from app.services.settings import put_setting

    cookie = _login_cookie(client, "admin", "admin12345")
    url = f"/api/v1/admin/instances/{terminal_instance}/terminal"

    # Enabled: open, use, close -> two audit rows.
    with client.websocket_connect(url, headers=cookie) as ws:
        assert json.loads(ws.receive_text())["type"] == "ready"
        ws.receive_bytes()
        ws.send_bytes(b"whoami\r")
        ws.receive_bytes()
    with SessionLocal() as db:
        actions = [
            row.action
            for row in db.query(AuditLog).filter(AuditLog.action.like("admin.terminal%")).all()
        ]
    assert "admin.terminal_open" in actions
    assert "admin.terminal_close" in actions

    # Disabled: the server refuses with an error frame instead of opening a PTY.
    with SessionLocal() as db:
        put_setting(db, "admin_terminal_enabled", False, None)
        db.commit()
    try:
        with client.websocket_connect(url, headers=cookie) as ws:
            error = json.loads(ws.receive_text())
            assert error["type"] == "error"
            assert "关闭" in error["message"]
        with SessionLocal() as db:
            denied = db.query(AuditLog).filter(AuditLog.action == "admin.terminal_denied").count()
        assert denied >= 1
    finally:
        with SessionLocal() as db:
            put_setting(db, "admin_terminal_enabled", True, None)
            db.commit()


def test_terminal_idle_timeout(client: TestClient, terminal_instance: int, monkeypatch):
    """An idle session is closed with an explanatory frame, not left hanging."""
    from app.config import settings

    monkeypatch.setattr(settings, "terminal_idle_seconds", 1)
    cookie = _login_cookie(client, "admin", "admin12345")
    url = f"/api/v1/admin/instances/{terminal_instance}/terminal"
    with client.websocket_connect(url, headers=cookie) as ws:
        assert json.loads(ws.receive_text())["type"] == "ready"
        ws.receive_bytes()  # banner
        import time

        time.sleep(1.3)
        message = json.loads(ws.receive_text())
        assert message["type"] == "error"
        assert "空闲" in message["message"]


def test_resize_exec_maps_rows_to_height():
    """docker-py names the arguments height/width; passing h/w raises TypeError.

    The exception used to be swallowed, so every resize silently failed and
    full-screen programs stayed at 80x24.
    """
    from app.drivers.docker_driver import DockerDriver

    calls: dict[str, object] = {}

    class Api:
        def exec_resize(self, exec_id, height=None, width=None):
            calls.update(exec_id=exec_id, height=height, width=width)

    class Client:
        api = Api()

    driver = DockerDriver()
    driver._client = Client()  # avoid a real daemon connection
    driver.resize_exec("exec-1", rows=30, cols=120)
    assert calls == {"exec_id": "exec-1", "height": 30, "width": 120}

    # An empty exec id (fallback path) is a no-op rather than an error.
    calls.clear()
    driver.resize_exec("", rows=30, cols=120)
    assert calls == {}
