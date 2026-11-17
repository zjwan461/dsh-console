from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.services.caddy import build_config


def _admin(client: TestClient) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin12345", "remember": True},
    )
    assert response.status_code == 200
    return response.json()["data"]["csrf_token"]


def _headers(csrf: str) -> dict[str, str]:
    return {"X-CSRF-Token": csrf}


def _register_image(client: TestClient, csrf: str) -> int:
    response = client.post(
        "/api/v1/admin/images",
        json={
            "image_ref": "ghcr.io/you/dsh:0.2.1-rc.5",
            "version": "0.2.1-rc.5",
            "changelog": "test image",
            "is_default": True,
        },
        headers=_headers(csrf),
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


def test_instance_lifecycle(client: TestClient):
    csrf = _admin(client)
    image_id = _register_image(client, csrf)

    created_invite = client.post(
        "/api/v1/admin/invites",
        json={"role": "user", "max_uses": 1, "expires_in_days": 1},
        headers=_headers(csrf),
    )
    code = created_invite.json()["data"]["code"]
    registered = client.post(
        "/api/v1/auth/invite/register",
        json={"code": code, "username": "bob", "password": "bobpass123"},
    )
    assert registered.status_code == 201, registered.text
    bob_csrf = registered.json()["data"]["csrf_token"]
    bob_id = registered.json()["data"]["user"]["id"]

    options = client.get("/api/v1/instances/me/options")
    assert options.status_code == 200
    assert options.json()["data"]["can_create"] is True
    assert options.json()["data"]["images"][0]["id"] == image_id

    created = client.post(
        "/api/v1/instances/me",
        json={"image_id": image_id},
        headers=_headers(bob_csrf),
    )
    assert created.status_code == 202, created.text
    assert created.json()["data"]["status"] == "running"

    current = client.get("/api/v1/instances/me")
    assert current.status_code == 200
    data = current.json()["data"]
    assert data["status"] == "running"
    assert data["url"] == "http://bob.dsh.example.com"
    instance_id = data["id"]

    # The Caddy document must route bob's host to the container and keep the
    # console route first.
    with SessionLocal() as db:
        config = build_config(db)
    routes = config["apps"]["http"]["servers"]["srv0"]["routes"]
    assert routes[0]["match"][0]["host"][0] == "console.dsh.example.com"
    assert any(r["match"][0]["host"] == ["bob.dsh.example.com"] for r in routes)
    bob_route = next(r for r in routes if r["match"][0]["host"] == ["bob.dsh.example.com"])
    assert bob_route["handle"][0]["upstreams"][0]["dial"] == f"dsh-u-{bob_id}:3080"

    launch = client.post("/api/v1/instances/me/launch", headers=_headers(bob_csrf))
    assert launch.status_code == 200, launch.text
    console_url = launch.json()["data"]["url"]
    assert "/go/" in console_url

    ticket_path = console_url.split("/go/", 1)[1]
    # /go is a browser entry point, not part of the versioned API.
    redirect = client.get(f"/go/{ticket_path}", follow_redirects=False)
    assert redirect.status_code == 302, redirect.text
    location = redirect.headers["location"]
    assert location.startswith("http://bob.dsh.example.com/?token=")
    assert "token=***" not in location
    assert redirect.headers["cache-control"] == "no-store"

    # The ticket is single-use.
    replay = client.get(f"/go/{ticket_path}", follow_redirects=False)
    assert replay.status_code == 410

    stopped = client.post("/api/v1/instances/me/stop", headers=_headers(bob_csrf))
    assert stopped.status_code == 202
    assert client.get("/api/v1/instances/me").json()["data"]["status"] == "stopped"

    with SessionLocal() as db:
        stopped_config = build_config(db)
    stopped_route = next(
        r
        for r in stopped_config["apps"]["http"]["servers"]["srv0"]["routes"]
        if r["match"][0]["host"] == ["bob.dsh.example.com"]
    )
    assert stopped_route["handle"][0]["handler"] == "subroute"
    assert stopped_route["handle"][0]["routes"][0]["match"][0]["path"] == ["/w/*"]

    started = client.post("/api/v1/instances/me/start", headers=_headers(bob_csrf))
    assert started.status_code == 202
    assert client.get("/api/v1/instances/me").json()["data"]["status"] == "running"

    csrf = _admin(client)
    admin_view = client.get("/api/v1/admin/instances")
    assert admin_view.status_code == 200
    assert any(item["id"] == instance_id for item in admin_view.json()["data"]["items"])

    deleted = client.delete(f"/api/v1/admin/instances/{instance_id}", headers=_headers(csrf))
    assert deleted.status_code == 204

    client.post(
        "/api/v1/auth/login",
        json={"username": "bob", "password": "bobpass123", "remember": True},
    )
    assert client.get("/api/v1/instances/me").json()["data"] is None
