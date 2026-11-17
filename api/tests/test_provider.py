from __future__ import annotations

import os
import time

import pytest
import yaml
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Instance, User


def _csrf(client: TestClient, username: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password, "remember": True},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["csrf_token"]


def _ensure_user(client: TestClient, username: str, password: str = "provider123") -> str:
    admin_csrf = _csrf(client, "admin", "admin12345")
    created = client.post(
        "/api/v1/admin/users",
        json={"username": username, "password": password, "role": "user"},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert created.status_code in (201, 409), created.text
    _ensure_default_image(client, admin_csrf)
    return _csrf(client, username, password)


def _ensure_default_image(client: TestClient, admin_csrf: str) -> None:
    """Other modules may have disabled the only image; make sure one is usable."""
    listed = client.get("/api/v1/admin/images").json()["data"]["items"]
    if not listed:
        client.post(
            "/api/v1/admin/images",
            json={
                "image_ref": "ghcr.io/you/dsh:provider",
                "version": "provider",
                "is_default": True,
            },
            headers={"X-CSRF-Token": admin_csrf},
        )
        return
    client.patch(
        f"/api/v1/admin/images/{listed[0]['id']}",
        json={"is_default": True, "status": "ready"},
        headers={"X-CSRF-Token": admin_csrf},
    )


def test_user_can_set_base_url_and_it_is_validated(client: TestClient):
    csrf = _ensure_user(client, "endpointuser")
    put = client.put(
        "/api/v1/me/credentials",
        json={"api_key": "sk-test-12345678", "base_url": "https://gateway.example.com/v1/"},
        headers={"X-CSRF-Token": csrf},
    )
    assert put.status_code == 200, put.text
    # The trailing slash is stripped for stable comparisons.
    assert put.json()["data"]["base_url"] == "https://gateway.example.com/v1"

    got = client.get("/api/v1/me/credentials").json()["data"]
    assert got["user_key"]["configured"] is True
    assert got["user_key"]["base_url"] == "https://gateway.example.com/v1"
    assert got["effective"] == "user"
    assert got["effective_base_url"] == "https://gateway.example.com/v1"

    # Bad endpoints are rejected before anything is stored.
    for bad in ("ftp://x/v1", "gateway.example.com", "https:///v1", "https://x/v1?a=1"):
        response = client.put(
            "/api/v1/me/credentials",
            json={"api_key": "sk-test-12345678", "base_url": bad},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 422, (bad, response.text)

    # Changing only the URL keeps the existing key.
    only_url = client.put(
        "/api/v1/me/credentials",
        json={"base_url": "http://litellm.internal:4000/v1"},
        headers={"X-CSRF-Token": csrf},
    )
    assert only_url.status_code == 200, only_url.text
    assert only_url.json()["data"]["hint"]  # key hint still present

    # Explicit clear (falls back to platform); clearing the URL alone keeps the key.
    endpoint_only = client.put(
        "/api/v1/me/credentials",
        json={"api_key": "", "base_url": ""},
        headers={"X-CSRF-Token": csrf},
    )
    assert endpoint_only.status_code == 200
    assert endpoint_only.json()["data"]["configured"] is True
    assert endpoint_only.json()["data"]["base_url"] is None

    cleared = client.put(
        "/api/v1/me/credentials",
        json={"clear_key": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert cleared.status_code == 200
    assert cleared.json()["data"]["configured"] is False


def test_base_url_only_without_key_is_rejected(client: TestClient):
    csrf = _ensure_user(client, "nounkey")
    response = client.put(
        "/api/v1/me/credentials",
        json={"base_url": "https://gateway.example.com/v1"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 422
    assert "先填写 API Key" in response.json()["error"]["message"]


def test_platform_base_url_is_returned_to_users(client: TestClient):
    admin_csrf = _csrf(client, "admin", "admin12345")
    put = client.put(
        "/api/v1/admin/credentials/platform",
        json={"api_key": "sk-platform-1234", "base_url": "https://platform.example.com/v1"},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert put.status_code == 200, put.text
    assert put.json()["data"]["base_url"] == "https://platform.example.com/v1"

    _ensure_user(client, "platformviewer")
    data = client.get("/api/v1/me/credentials").json()["data"]
    assert data["platform_key_configured"] is True
    assert data["platform_base_url"] == "https://platform.example.com/v1"
    assert data["effective"] == "platform"

    # Put the platform credential back to "unset" for other modules.
    client.put(
        "/api/v1/admin/credentials/platform",
        json={"clear_key": True},
        headers={"X-CSRF-Token": admin_csrf},
    )


def _instance_for(username: str) -> Instance:
    with SessionLocal() as db:
        user = db.query(User).filter(User.username == username).one()
        return db.query(Instance).filter(Instance.user_id == user.id).one()


def test_provisioning_writes_endpoint_into_settings_yaml(client: TestClient):
    csrf = _ensure_user(client, "yamluser")
    base = "https://gateway.example.com/v1"
    assert (
        client.put(
            "/api/v1/me/credentials",
            json={"api_key": "sk-yaml-12345678", "base_url": base},
            headers={"X-CSRF-Token": csrf},
        ).status_code
        == 200
    )
    created = client.post("/api/v1/instances/me", json={}, headers={"X-CSRF-Token": csrf})
    assert created.status_code == 202, created.text

    instance = _instance_for("yamluser")
    path = os.path.join(instance.data_path, "settings.yaml")
    assert os.path.exists(path)
    data = yaml.safe_load(open(path, encoding="utf-8"))
    assert data["llm-deepseek"]["baseURL"] == base


def test_settings_yaml_merge_preserves_user_sections(client: TestClient):
    """A user may have configured other providers in the DSH UI; we must not wipe it."""
    csrf = _ensure_user(client, "mergeuser")
    client.put(
        "/api/v1/me/credentials",
        json={"api_key": "sk-merge-12345678", "base_url": "https://gw.example.com/v1"},
        headers={"X-CSRF-Token": csrf},
    )
    client.post("/api/v1/instances/me", json={}, headers={"X-CSRF-Token": csrf})
    instance = _instance_for("mergeuser")
    path = os.path.join(instance.data_path, "settings.yaml")

    # Simulate an extra provider the user added by hand, then restart.
    data = yaml.safe_load(open(path, encoding="utf-8")) or {}
    data["llm-pi-ai"] = {"providers": {"vllm-local": {"baseURL": "http://vllm:8000/v1"}}}
    yaml.safe_dump(data, open(path, "w", encoding="utf-8"), allow_unicode=True)

    restarted = client.post("/api/v1/instances/me/restart", json={}, headers={"X-CSRF-Token": csrf})
    assert restarted.status_code in (200, 202), restarted.text

    merged = yaml.safe_load(open(path, encoding="utf-8"))
    assert merged["llm-deepseek"]["baseURL"] == "https://gw.example.com/v1"
    assert merged["llm-pi-ai"]["providers"]["vllm-local"]["baseURL"] == "http://vllm:8000/v1"


def test_changing_endpoint_recreates_container_on_restart(client: TestClient):
    """Env/settings are baked at create time, so a credential change needs a new container."""
    csrf = _ensure_user(client, "recreateuser")
    client.put(
        "/api/v1/me/credentials",
        json={"api_key": "sk-recreate-1234", "base_url": "https://one.example.com/v1"},
        headers={"X-CSRF-Token": csrf},
    )
    created = client.post("/api/v1/instances/me", json={}, headers={"X-CSRF-Token": csrf})
    assert created.status_code == 202, created.text
    first = _instance_for("recreateuser").container_id
    assert first

    # Restart without changes: the container is reused.
    client.post("/api/v1/instances/me/restart", json={}, headers={"X-CSRF-Token": csrf})
    assert _instance_for("recreateuser").container_id == first

    # Change the endpoint, restart: a new container is created.
    client.put(
        "/api/v1/me/credentials",
        json={"base_url": "https://two.example.com/v1"},
        headers={"X-CSRF-Token": csrf},
    )
    client.post("/api/v1/instances/me/restart", json={}, headers={"X-CSRF-Token": csrf})
    second = _instance_for("recreateuser").container_id
    assert second and second != first

    # And the new settings are visible in the instance data.
    instance = _instance_for("recreateuser")
    data = yaml.safe_load(open(os.path.join(instance.data_path, "settings.yaml"), encoding="utf-8"))
    assert data["llm-deepseek"]["baseURL"] == "https://two.example.com/v1"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """FakeDriver is instant; keep the test runtime independent of real polling."""
    time.sleep(0)
