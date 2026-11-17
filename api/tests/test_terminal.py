from __future__ import annotations

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
    (that surfaced as a 500 during the handshake) and must require admin."""
    cookie = _login_cookie(client, "admin", "admin12345")
    url = f"/api/v1/admin/instances/{terminal_instance}/terminal"
    with client.websocket_connect(url, headers=cookie) as ws:
        hello = ws.receive_text()
        assert "terminal" in hello.lower(), hello
        ws.send_text("echo hi")
        assert "echo hi" in ws.receive_text()


def test_terminal_rejects_non_admin(client: TestClient, terminal_instance: int):
    cookie = _login_cookie(client, "termuser", "termpass123")
    url = f"/api/v1/admin/instances/{terminal_instance}/terminal"
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(url, headers=cookie) as ws:
            ws.receive_text()
