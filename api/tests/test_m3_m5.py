from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Instance, Snapshot, UsageBucket, User, UserQuota
from app.services import metering, node_metrics, worker_tasks


def _login(client: TestClient, username: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password, "remember": True},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["csrf_token"]


def _headers(csrf: str) -> dict[str, str]:
    return {"X-CSRF-Token": csrf}


@pytest.fixture(scope="module")
def ctx(client: TestClient):
    admin_csrf = _login(client, "admin", "admin12345")
    image = client.post(
        "/api/v1/admin/images",
        json={
            "image_ref": "ghcr.io/you/dsh:0.2.1-rc.5",
            "version": "0.2.1-rc.5",
            "is_default": True,
        },
        headers=_headers(admin_csrf),
    )
    assert image.status_code == 201, image.text
    image_id = image.json()["data"]["id"]

    created = client.post(
        "/api/v1/admin/users",
        json={
            "username": "alice",
            "password": "alicepass1",
            "display_name": "Alice",
            "role": "user",
            "must_change_password": False,
        },
        headers=_headers(admin_csrf),
    )
    assert created.status_code == 201, created.text

    alice_csrf = _login(client, "alice", "alicepass1")
    instance = client.post(
        "/api/v1/instances/me",
        json={"image_id": image_id},
        headers=_headers(alice_csrf),
    )
    assert instance.status_code == 202, instance.text
    with SessionLocal() as db:
        alice = db.query(User).filter(User.username == "alice").one()
        inst = db.query(Instance).filter(Instance.user_id == alice.id).one()
        workspace = inst.workspace_path
        instance_id = inst.id
    os.makedirs(workspace, exist_ok=True)
    with open(os.path.join(workspace, "hello.txt"), "w", encoding="utf-8") as fh:
        fh.write("v1")
    return {
        "admin_csrf": admin_csrf,
        "alice_csrf": alice_csrf,
        "image_id": image_id,
        "instance_id": instance_id,
        "workspace": workspace,
    }


def test_metering_flush_and_usage_api(client: TestClient, ctx):
    with SessionLocal() as db:
        inst = db.get(Instance, ctx["instance_id"])
        metering.record(
            inst.id,
            inst.user_id,
            {"cpu_percent": 50, "mem_used_bytes": 512 * 1024**2},
            30,
            active=True,
            net_rx_delta=100,
            net_tx_delta=200,
        )
        result = metering.flush(db)
        assert result["buckets"] >= 1
        bucket = db.query(UsageBucket).filter(UsageBucket.instance_id == inst.id).first()
        assert bucket is not None
        assert bucket.active_seconds == 30
        assert bucket.net_rx_bytes == 100
        quota = db.get(UserQuota, inst.user_id)
        assert quota.seconds_used >= 30

    usage = client.get("/api/v1/me/usage?granularity=hour")
    assert usage.status_code == 200
    assert usage.json()["data"]["summary"]["active_seconds"] >= 30


def test_snapshot_create_and_restore(client: TestClient, ctx):
    created = client.post(
        "/api/v1/instances/me/snapshots",
        json={"note": "before change"},
        headers=_headers(ctx["alice_csrf"]),
    )
    assert created.status_code == 202, created.text
    snapshot_id = created.json()["data"]["id"]

    with SessionLocal() as db:
        snapshot = db.get(Snapshot, snapshot_id)
        assert snapshot.status.value == "ready", snapshot.error
        assert snapshot.size_bytes > 0

    with open(os.path.join(ctx["workspace"], "hello.txt"), "w", encoding="utf-8") as fh:
        fh.write("v2")

    restored = client.post(
        f"/api/v1/snapshots/{snapshot_id}/restore", headers=_headers(ctx["alice_csrf"])
    )
    assert restored.status_code == 202, restored.text
    with open(os.path.join(ctx["workspace"], "hello.txt"), encoding="utf-8") as fh:
        assert fh.read() == "v1"


def test_files_crud_and_traversal(client: TestClient, ctx):
    listed = client.get("/api/v1/instances/me/files?path=/")
    assert listed.status_code == 200
    assert any(entry["name"] == "hello.txt" for entry in listed.json()["data"]["entries"])

    made = client.post(
        "/api/v1/instances/me/files/mkdir",
        json={"path": "/", "name": "docs"},
        headers=_headers(ctx["alice_csrf"]),
    )
    assert made.status_code == 201, made.text

    uploaded = client.post(
        "/api/v1/instances/me/files/upload",
        data={"path": "/docs"},
        files={"file": ("note.txt", b"hello upload", "text/plain")},
        headers=_headers(ctx["alice_csrf"]),
    )
    assert uploaded.status_code == 201, uploaded.text

    downloaded = client.get("/api/v1/instances/me/files/download?path=/docs/note.txt")
    assert downloaded.status_code == 200
    assert downloaded.content == b"hello upload"

    renamed = client.post(
        "/api/v1/instances/me/files/rename",
        json={"path": "/docs/note.txt", "new_name": "note2.txt"},
        headers=_headers(ctx["alice_csrf"]),
    )
    assert renamed.status_code == 200, renamed.text

    traversal = client.get("/api/v1/instances/me/files?path=/../../etc")
    assert traversal.status_code == 400

    deleted = client.delete(
        "/api/v1/instances/me/files?path=/docs&recursive=true",
        headers=_headers(ctx["alice_csrf"]),
    )
    assert deleted.status_code == 204


def test_user_image_commit_and_rebuild(client: TestClient, ctx):
    committed = client.post(
        "/api/v1/instances/me/images",
        json={"name": "pandas-env", "note": "test"},
        headers=_headers(ctx["alice_csrf"]),
    )
    assert committed.status_code == 202, committed.text
    image_id = committed.json()["data"]["id"]

    rebuilt = client.post(
        f"/api/v1/me/images/{image_id}/rebuild", headers=_headers(ctx["alice_csrf"])
    )
    assert rebuilt.status_code == 202, rebuilt.text
    current = client.get("/api/v1/instances/me")
    assert current.json()["data"]["status"] == "running"


def test_tickets_and_announcements(client: TestClient, ctx):
    admin_csrf = _login(client, "admin", "admin12345")
    announcement = client.post(
        "/api/v1/admin/announcements",
        json={
            "title": "维护通知",
            "body_md": "今晚维护",
            "level": "maintenance",
            "status": "published",
            "pinned": True,
        },
        headers=_headers(admin_csrf),
    )
    assert announcement.status_code == 201, announcement.text

    alice_csrf = _login(client, "alice", "alicepass1")
    mine = client.get("/api/v1/me/announcements")
    assert mine.status_code == 200
    assert any(item["title"] == "维护通知" for item in mine.json()["data"]["items"])

    ticket = client.post(
        "/api/v1/me/tickets",
        json={"subject": "启动慢", "category": "bug", "priority": "normal", "body": "第一次很慢"},
        headers=_headers(alice_csrf),
    )
    assert ticket.status_code == 201, ticket.text
    ticket_id = ticket.json()["data"]["id"]

    admin_csrf = _login(client, "admin", "admin12345")
    reply = client.post(
        f"/api/v1/admin/tickets/{ticket_id}/messages",
        json={"body": "正在排查"},
        headers=_headers(admin_csrf),
    )
    assert reply.status_code == 201, reply.text

    _login(client, "alice", "alicepass1")
    detail = client.get(f"/api/v1/me/tickets/{ticket_id}")
    assert len(detail.json()["data"]["messages"]) == 2


def test_node_metrics_and_worker_idle(client: TestClient, ctx):
    with SessionLocal() as db:
        metric = node_metrics.collect(db)
        assert metric is not None
        inst = db.get(Instance, ctx["instance_id"])
        inst.last_active_at = datetime.utcnow() - timedelta(hours=2)
        inst.idle_timeout_seconds = 60
        db.commit()
        from app.redis import get_redis

        get_redis().delete(f"inst:last_active:{inst.id}")

    with SessionLocal() as db:
        slept = worker_tasks.idle_once(db)
        assert slept >= 1
        inst = db.get(Instance, ctx["instance_id"])
        assert inst.status.value == "sleeping"
        assert inst.status_reason == "idle_timeout"
