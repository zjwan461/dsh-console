from __future__ import annotations

import io
import json
import os
import zipfile

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Instance, User

BUNDLE_PACKAGE = {
    "name": "@local/demo-plugin",
    "version": "1.0.0",
    "private": True,
    "type": "module",
    "exports": {".": "./index.js"},
    "dsh": {"bundle": {"patch": "./cordis.patch.yml"}},
}
PATCH = "- insert:\n    - id: demo-plugin\n      name: '@local/demo-plugin'\n"


def _login(client: TestClient, username: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password, "remember": True},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["csrf_token"]


def _admin(client: TestClient) -> str:
    return _login(client, "admin", "admin12345")


def _ensure_image(client: TestClient, csrf: str) -> None:
    items = client.get("/api/v1/admin/images").json()["data"]["items"]
    if not items:
        client.post(
            "/api/v1/admin/images",
            json={"image_ref": "ghcr.io/you/dsh:plugins", "version": "plugins", "is_default": True},
            headers={"X-CSRF-Token": csrf},
        )
        return
    client.patch(
        f"/api/v1/admin/images/{items[0]['id']}",
        json={"is_default": True, "status": "ready"},
        headers={"X-CSRF-Token": csrf},
    )


def _ensure_user(client: TestClient, username: str, password: str = "pluginpass123") -> str:
    csrf = _admin(client)
    created = client.post(
        "/api/v1/admin/users",
        json={"username": username, "password": password, "role": "user"},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code in (201, 409), created.text
    _ensure_image(client, csrf)
    return _login(client, username, password)


def _bundle(version: str = "1.0.0", name: str = "@local/demo-plugin") -> bytes:
    package = dict(BUNDLE_PACKAGE)
    package["version"] = version
    package["name"] = name
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("demo/package.json", json.dumps(package))
        archive.writestr("demo/index.js", "export default function () {}\n")
        archive.writestr("demo/cordis.patch.yml", PATCH)
    return buffer.getvalue()


def _import(client: TestClient, csrf: str, data: bytes) -> dict:
    response = client.post(
        "/api/v1/admin/plugins/import",
        files={"file": ("plugin.zip", data, "application/zip")},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _instance_for(username: str) -> Instance:
    with SessionLocal() as db:
        user = db.query(User).filter(User.username == username).one()
        return db.query(Instance).filter(Instance.user_id == user.id).one()


def test_import_validates_bundle_and_rejects_bad_entry(client: TestClient):
    csrf = _admin(client)
    plugin = _import(client, csrf, _bundle())
    assert plugin["package_name"] == "@local/demo-plugin"
    assert plugin["version"] == "1.0.0"
    assert plugin["status"] == "disabled"

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        bad = dict(BUNDLE_PACKAGE)
        bad["exports"] = {".": "./missing.js"}
        archive.writestr("package.json", json.dumps(bad))
        archive.writestr("cordis.patch.yml", PATCH)
    bad_response = client.post(
        "/api/v1/admin/plugins/import",
        files={"file": ("bad.zip", buffer.getvalue(), "application/zip")},
        headers={"X-CSRF-Token": csrf},
    )
    assert bad_response.status_code == 422, bad_response.text


def test_assign_plugin_copies_bundle_mounts_it_and_marks_restart(client: TestClient):
    csrf = _admin(client)
    plugin = _import(client, csrf, _bundle(version="1.1.0", name="@local/assign-plugin"))

    user_csrf = _ensure_user(client, "pluginsyncuser")
    created = client.post("/api/v1/instances/me", json={}, headers={"X-CSRF-Token": user_csrf})
    assert created.status_code == 202, created.text

    # Logging in as the user replaced the cookie; re-auth as admin.
    csrf = _admin(client)
    # Enable + grant to everyone.
    assert client.post(
        f"/api/v1/admin/plugins/{plugin['id']}/enable", headers={"X-CSRF-Token": csrf}
    ).status_code == 200
    assert client.put(
        f"/api/v1/admin/plugins/{plugin['id']}/grants",
        json={"grants": [{"scope": "all", "target_id": 0}]},
        headers={"X-CSRF-Token": csrf},
    ).status_code == 200

    instance = _instance_for("pluginsyncuser")
    folder = os.path.join(instance.data_path, "plugins", "local__assign-plugin")
    assert os.path.isfile(os.path.join(folder, "package.json"))
    assert os.path.isfile(os.path.join(folder, "cordis.patch.yml"))

    with SessionLocal() as db:
        instance = db.get(Instance, instance.id)
        assert instance.needs_restart is True
        assert instance.plugins_revision

    # Restart applies the new mount and clears the "needs restart" flag.
    user_csrf = _login(client, "pluginsyncuser", "pluginpass123")
    restarted = client.post(
        "/api/v1/instances/me/restart", json={}, headers={"X-CSRF-Token": user_csrf}
    )
    assert restarted.status_code in (200, 202), restarted.text
    instance = _instance_for("pluginsyncuser")
    assert instance.needs_restart is False
    from app.drivers.fake_driver import FakeDriver

    spec = FakeDriver.containers[instance.container_id]["spec"]
    targets = [target for _host, target, _ro in spec.mounts]
    assert "/opt/dsh-plugins/local__assign-plugin" in targets


def test_disable_removes_patch_block_and_directory(client: TestClient):
    csrf = _admin(client)
    plugin = _import(client, csrf, _bundle(version="2.0.0"))
    user_csrf = _ensure_user(client, "plugindisableuser")
    client.post("/api/v1/instances/me", json={}, headers={"X-CSRF-Token": user_csrf})
    instance = _instance_for("plugindisableuser")
    csrf = _admin(client)

    # Simulate the entrypoint having seeded the patch layer.
    patch_path = os.path.join(instance.data_path, "cordis.patch.yml")
    open(patch_path, "w", encoding="utf-8").write(
        "# --- bundled plugins (seeded once by entrypoint.sh; edit freely) ---\n"
        "# bundled plugin: @local/demo-plugin\n" + PATCH
    )

    client.post(f"/api/v1/admin/plugins/{plugin['id']}/enable", headers={"X-CSRF-Token": csrf})
    client.put(
        f"/api/v1/admin/plugins/{plugin['id']}/grants",
        json={"grants": [{"scope": "all", "target_id": 0}]},
        headers={"X-CSRF-Token": csrf},
    )
    folder = os.path.join(instance.data_path, "plugins", "local__demo-plugin")
    assert os.path.isdir(folder)

    client.post(f"/api/v1/admin/plugins/{plugin['id']}/disable", headers={"X-CSRF-Token": csrf})
    assert not os.path.exists(folder)
    text = open(patch_path, encoding="utf-8").read()
    assert "@local/demo-plugin" not in text
    assert "bundled plugins" in text or text.strip() == ""


def test_plugin_patch_removal_keeps_other_blocks():
    from app.services.plugins import remove_plugin_block

    text = (
        "# --- bundled plugins (seeded once by entrypoint.sh; edit freely) ---\n"
        "# bundled plugin: @local/a\n- insert:\n    - id: a\n"
        "# bundled plugin: @local/b\n- insert:\n    - id: b\n"
    )
    result, removed = remove_plugin_block(text, "@local/a")
    assert removed is True
    assert "@local/a" not in result
    assert "@local/b" in result
    assert "id: b" in result

    same, removed_again = remove_plugin_block(result, "@local/a")
    assert removed_again is False
    assert same == result


def test_user_can_see_assigned_plugins_but_not_toggle(client: TestClient):
    csrf = _admin(client)
    plugin = _import(client, csrf, _bundle(version="3.0.0", name="@local/readonly-plugin"))
    user_csrf = _ensure_user(client, "pluginviewer")
    client.post("/api/v1/instances/me", json={}, headers={"X-CSRF-Token": user_csrf})
    csrf = _admin(client)
    client.post(f"/api/v1/admin/plugins/{plugin['id']}/enable", headers={"X-CSRF-Token": csrf})
    client.put(
        f"/api/v1/admin/plugins/{plugin['id']}/grants",
        json={"grants": [{"scope": "all", "target_id": 0}]},
        headers={"X-CSRF-Token": csrf},
    )

    _login(client, "pluginviewer", "pluginpass123")
    data = client.get("/api/v1/me/plugins").json()["data"]
    names = [item["package_name"] for item in data["items"]]
    assert "@local/readonly-plugin" in names
    # No PUT endpoint: the read-only view is the only user-facing surface.
    assert client.put(
        "/api/v1/me/plugins", json={}, headers={"X-CSRF-Token": user_csrf}
    ).status_code in (404, 405)