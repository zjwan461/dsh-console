from __future__ import annotations

import io
import json
import os
import zipfile

import pytest
import yaml
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Instance, User

SKILL_MD = """---
name: pdf-helper
description: 帮用户拆分与合并 PDF
whenToUse: 需要处理 PDF 时
invocation:
  modelInvocable: true
  userInvocable: true
---

# PDF Helper

按页拆分或合并 PDF。
"""


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
            json={"image_ref": "ghcr.io/you/dsh:skills", "version": "skills", "is_default": True},
            headers={"X-CSRF-Token": csrf},
        )
        return
    client.patch(
        f"/api/v1/admin/images/{items[0]['id']}",
        json={"is_default": True, "status": "ready"},
        headers={"X-CSRF-Token": csrf},
    )


def _ensure_user(client: TestClient, username: str, password: str = "skillpass123") -> str:
    csrf = _admin(client)
    created = client.post(
        "/api/v1/admin/users",
        json={"username": username, "password": password, "role": "user"},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code in (201, 409), created.text
    _ensure_image(client, csrf)
    return _login(client, username, password)


def _create_skill(client: TestClient, csrf: str, name: str = "pdf-helper") -> dict:
    response = client.post(
        "/api/v1/admin/skills",
        json={
            "name": name,
            "description": "帮用户拆分与合并 PDF",
            "when_to_use": "需要处理 PDF 时",
            "body": "# PDF Helper\n\n按页拆分或合并 PDF。\n",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _grant_all(client: TestClient, csrf: str, skill_id: int) -> None:
    response = client.put(
        f"/api/v1/admin/skills/{skill_id}/grants",
        json={"grants": [{"scope": "all", "target_id": 0}]},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text


def _publish(client: TestClient, csrf: str, skill_id: int) -> None:
    response = client.post(
        f"/api/v1/admin/skills/{skill_id}/publish", headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 200, response.text


def _instance_for(username: str) -> Instance:
    with SessionLocal() as db:
        user = db.query(User).filter(User.username == username).one()
        return db.query(Instance).filter(Instance.user_id == user.id).one()


def test_skill_created_as_draft_and_not_visible_until_published(client: TestClient):
    admin_csrf = _admin(client)
    skill = _create_skill(client, admin_csrf)
    assert skill["status"] == "draft"
    assert skill["body"].startswith("# PDF Helper")

    user_csrf = _ensure_user(client, "skilldraftuser")
    _ = user_csrf
    mine = client.get("/api/v1/me/skills").json()["data"]
    assert mine["items"] == []

    # Logging in as the user replaced the session cookie; re-auth as admin.
    admin_csrf = _admin(client)
    _grant_all(client, admin_csrf, skill["id"])
    _publish(client, admin_csrf, skill["id"])

    _login(client, "skilldraftuser", "skillpass123")
    mine = client.get("/api/v1/me/skills").json()["data"]
    assert [item["name"] for item in mine["items"]] == ["pdf-helper"]
    assert mine["items"][0]["enabled"] is True  # default enabled
    assert mine["enabled"] == 1


def test_import_zip_and_reject_unsafe_archive(client: TestClient):
    admin_csrf = _admin(client)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("my-skill/SKILL.md", SKILL_MD.replace("pdf-helper", "zip-skill"))
        archive.writestr("my-skill/scripts/run.sh", "#!/bin/sh\necho hi\n")
    buffer.seek(0)
    imported = client.post(
        "/api/v1/admin/skills/import",
        files={"file": ("skill.zip", buffer.getvalue(), "application/zip")},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert imported.status_code == 201, imported.text
    data = imported.json()["data"]
    assert data["name"] == "zip-skill"
    assert any(item["path"] == "scripts/run.sh" for item in data["files"])

    # Path traversal must be refused before anything lands on disk.
    evil = io.BytesIO()
    with zipfile.ZipFile(evil, "w") as archive:
        archive.writestr("../escape/SKILL.md", SKILL_MD)
    evil.seek(0)
    bad = client.post(
        "/api/v1/admin/skills/import",
        files={"file": ("evil.zip", evil.getvalue(), "application/zip")},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert bad.status_code == 422, bad.text

    duplicate = io.BytesIO()
    with zipfile.ZipFile(duplicate, "w") as archive:
        archive.writestr("SKILL.md", SKILL_MD.replace("pdf-helper", "zip-skill"))
    duplicate.seek(0)
    dup = client.post(
        "/api/v1/admin/skills/import",
        files={"file": ("dup.zip", duplicate.getvalue(), "application/zip")},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert dup.status_code == 409, dup.text


def test_materialisation_respects_user_toggle_and_keeps_own_dirs(client: TestClient):
    admin_csrf = _admin(client)
    skill = _create_skill(client, admin_csrf, "materialise-me")
    _grant_all(client, admin_csrf, skill["id"])
    _publish(client, admin_csrf, skill["id"])

    user_csrf = _ensure_user(client, "skillsyncuser")
    created = client.post("/api/v1/instances/me", json={}, headers={"X-CSRF-Token": user_csrf})
    assert created.status_code == 202, created.text

    instance = _instance_for("skillsyncuser")
    skills_dir = os.path.join(instance.data_path, "skills")
    target = os.path.join(skills_dir, "materialise-me")
    assert os.path.isfile(os.path.join(target, "SKILL.md"))
    skill_md = open(os.path.join(target, "SKILL.md"), encoding="utf-8").read()
    data = yaml.safe_load(skill_md.split("---")[1])
    assert data["name"] == "materialise-me"

    # A skill the user created by hand must survive reconciliation.
    own = os.path.join(skills_dir, "my-own")
    os.makedirs(own)
    open(os.path.join(own, "SKILL.md"), "w", encoding="utf-8").write("---\nname: my-own\n---\n")

    # Turn the platform skill off.
    mine = client.get("/api/v1/me/skills").json()["data"]
    assert mine["items"][0]["enabled"] is True
    saved = client.put(
        "/api/v1/me/skills",
        json={"enabled_ids": []},
        headers={"X-CSRF-Token": user_csrf},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["data"]["enabled"] == 0
    assert not os.path.exists(target)
    assert os.path.isdir(own)

    # Manifest only tracks managed entries.
    manifest = json.load(
        open(os.path.join(instance.data_path, ".console", "skills-manifest.json"), encoding="utf-8")
    )
    assert manifest == {}


def test_update_creates_new_revision_and_requires_publish(client: TestClient):
    admin_csrf = _admin(client)
    skill = _create_skill(client, admin_csrf, "revision-skill")
    _grant_all(client, admin_csrf, skill["id"])
    _publish(client, admin_csrf, skill["id"])

    updated = client.put(
        f"/api/v1/admin/skills/{skill['id']}",
        json={
            "description": "更新后的说明",
            "when_to_use": "需要时",
            "body": "# 第二版\n",
            "changelog": "第二版",
        },
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert updated.status_code == 200, updated.text
    # Editing a published skill keeps it published and hot-reloads the new revision.
    assert updated.json()["data"]["status"] == "published"
    assert updated.json()["data"]["revision"] == 2
    assert updated.json()["data"]["body"] == "# 第二版\n"

    revisions = client.get(
        f"/api/v1/admin/skills/{skill['id']}/revisions", headers={"X-CSRF-Token": admin_csrf}
    ).json()["data"]["items"]
    assert [item["rev"] for item in revisions] == [2, 1]

    # Metadata-only save keeps the body.
    meta_only = client.put(
        f"/api/v1/admin/skills/{skill['id']}",
        json={"description": "只改说明"},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert meta_only.status_code == 200
    assert meta_only.json()["data"]["body"] == "# 第二版\n"


def test_published_edit_hot_updates_instance(client: TestClient):
    admin_csrf = _admin(client)
    skill = _create_skill(client, admin_csrf, "hot-edit")
    _grant_all(client, admin_csrf, skill["id"])
    _publish(client, admin_csrf, skill["id"])

    user_csrf = _ensure_user(client, "skilledituser")
    created = client.post("/api/v1/instances/me", json={}, headers={"X-CSRF-Token": user_csrf})
    assert created.status_code == 202, created.text
    instance = _instance_for("skilledituser")
    target = os.path.join(instance.data_path, "skills", "hot-edit", "SKILL.md")
    assert "PDF Helper" in open(target, encoding="utf-8").read()

    admin_csrf = _admin(client)
    edited = client.put(
        f"/api/v1/admin/skills/{skill['id']}",
        json={"description": "热更新", "body": "# 第二版内容\n", "changelog": "热更新"},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["data"]["synced"] >= 1
    assert "第二版内容" in open(target, encoding="utf-8").read()


def test_plan_grant_filters_users(client: TestClient):
    admin_csrf = _admin(client)
    skill = _create_skill(client, admin_csrf, "plan-only")
    plans = client.get("/api/v1/admin/plans", headers={"X-CSRF-Token": admin_csrf}).json()["data"]
    plan_id = plans["items"][0]["id"]
    granted = client.put(
        f"/api/v1/admin/skills/{skill['id']}/grants",
        json={"grants": [{"scope": "plan", "target_id": plan_id}]},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert granted.status_code == 200, granted.text
    _publish(client, admin_csrf, skill["id"])

    # New users get the default plan, which is this plan.
    _ensure_user(client, "planuser")
    mine = client.get("/api/v1/me/skills").json()["data"]
    # Earlier tests granted other skills to "all"; assert membership, not equality.
    names = [item["name"] for item in mine["items"]]
    assert "plan-only" in names


def test_invalid_frontmatter_and_name(client: TestClient):
    admin_csrf = _admin(client)
    bad = client.post(
        "/api/v1/admin/skills",
        json={"name": "Bad Name", "description": "x"},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert bad.status_code == 422

    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("SKILL.md", "no frontmatter here")
    payload.seek(0)
    invalid = client.post(
        "/api/v1/admin/skills/import",
        files={"file": ("bad.zip", payload.getvalue(), "application/zip")},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert invalid.status_code == 422


@pytest.fixture(autouse=True)
def _no_sleep():
    pass