from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    db_path = "test_console.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    with TestClient(app) as test_client:
        yield test_client
    # The SQLite engine keeps the file handle open until process exit; leaving the
    # file behind is fine (it is recreated at the start of the next run).


def login(client: TestClient, username: str = "admin", password: str = "admin12345") -> str:
    response = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password, "remember": True}
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["csrf_token"]


def test_health(client: TestClient):
    assert client.get("/healthz").json()["status"] == "ok"
    assert client.get("/readyz").json()["status"] == "ok"


def test_auth_and_admin_flow(client: TestClient):
    csrf = login(client)
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["data"]["user"]["role"] == "admin"

    # Unauthorised writes and missing CSRF are rejected.
    no_csrf = client.post("/api/v1/admin/invites", json={"max_uses": 2})
    assert no_csrf.status_code == 403
    assert no_csrf.json()["error"]["code"] == "AUTH_CSRF_INVALID"

    created = client.post(
        "/api/v1/admin/invites",
        json={"role": "user", "max_uses": 2, "expires_in_days": 3, "note": "test"},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text
    code = created.json()["data"]["code"]

    # Register a new user through the invite (no CSRF needed).
    registered = client.post(
        "/api/v1/auth/invite/register",
        json={"code": code, "username": "alice", "password": "alicepass1", "display_name": "Alice"},
    )
    assert registered.status_code == 201, registered.text
    alice_csrf = registered.json()["data"]["csrf_token"]

    # Alice cannot reach admin endpoints.
    forbidden = client.get("/api/v1/admin/users")
    assert forbidden.status_code == 403

    client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": alice_csrf})
    csrf = login(client)

    users = client.get("/api/v1/admin/users")
    assert users.status_code == 200
    assert users.json()["data"]["total"] >= 2
    alice = next(u for u in users.json()["data"]["items"] if u["username"] == "alice")

    granted = client.post(
        f"/api/v1/admin/quotas/{alice['id']}/grant",
        json={"minutes": 120, "reason": "smoke test"},
        headers={"X-CSRF-Token": csrf},
    )
    assert granted.status_code == 200, granted.text
    assert granted.json()["data"]["extra_minutes"] == 120
    assert granted.json()["data"]["remaining_minutes"] > 0


def test_user_cannot_disable_self(client: TestClient):
    csrf = login(client)
    response = client.get("/api/v1/admin/users")
    admin = next(u for u in response.json()["data"]["items"] if u["username"] == "admin")
    blocked = client.patch(
        f"/api/v1/admin/users/{admin['id']}",
        json={"status": "disabled"},
        headers={"X-CSRF-Token": csrf},
    )
    assert blocked.status_code == 403


def test_settings_and_audit(client: TestClient):
    csrf = login(client)
    settings = client.get("/api/v1/admin/settings")
    assert settings.status_code == 200
    assert settings.json()["data"]["base_domain"] == "dsh.example.com"

    updated = client.put(
        "/api/v1/admin/settings",
        json={"console_name": "测试控制台"},
        headers={"X-CSRF-Token": csrf},
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["console_name"] == "测试控制台"

    audit = client.get("/api/v1/admin/audit")
    assert audit.status_code == 200
    assert audit.json()["data"]["total"] > 0
