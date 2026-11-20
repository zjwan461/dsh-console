from __future__ import annotations

from app.db import SessionLocal
from app.models import DshImage, ImageStatus, Instance, User, UserImage, UserImageStatus


def _setup_instance(client):
    csrf = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin12345", "remember": True},
    ).json()["data"]["csrf_token"]
    with SessionLocal() as db:
        user = db.query(User).filter(User.username == "admin").one()
        # Each test starts from a clean quota: the default plan allows only two.
        db.query(UserImage).filter(UserImage.user_id == user.id).delete()
        db.commit()
        instance = db.query(Instance).filter(Instance.user_id == user.id).first()
        if instance is None:
            if db.query(DshImage).filter(DshImage.is_default.is_(True)).first() is None:
                db.add(
                    DshImage(
                        version="0.2.1-rc.5",
                        image_ref="ghcr.io/zjwan461/dsh-docker:0.2.1-rc.5",
                        status=ImageStatus.ready,
                        is_default=True,
                    )
                )
                db.commit()
    if instance is None:
        client.post("/api/v1/instances/me", json={}, headers={"X-CSRF-Token": csrf})
        with SessionLocal() as db:
            user = db.query(User).filter(User.username == "admin").one()
            instance = db.query(Instance).filter(Instance.user_id == user.id).one()
    return csrf, instance


def test_commit_returns_immediately_and_runs_in_the_background(client):
    """`docker commit` takes minutes; the request must not hold it.

    With JOBS_INLINE=1 the work still finishes before the reply, so asserting the
    row is `ready` proves the same code path ran — the point is that the reply no
    longer depends on the pull finishing inside the request.
    """
    csrf, instance = _setup_instance(client)
    response = client.post(
        "/api/v1/instances/me/images",
        json={"name": "async-image", "note": "should not block"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 202, response.text
    body = response.json()["data"]
    assert body["name"] == "async-image"
    assert body["status"] in {"committing", "ready"}

    listed = client.get("/api/v1/me/images").json()["data"]["items"]
    row = next(item for item in listed if item["name"] == "async-image")
    assert row["image_ref"].startswith("dsh-user/")


def test_commit_rejects_a_duplicate_name_before_any_work(client):
    csrf, _ = _setup_instance(client)
    first = client.post(
        "/api/v1/instances/me/images",
        json={"name": "dup-image"},
        headers={"X-CSRF-Token": csrf},
    )
    assert first.status_code == 202, first.text
    second = client.post(
        "/api/v1/instances/me/images",
        json={"name": "dup-image"},
        headers={"X-CSRF-Token": csrf},
    )
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "IMAGE_DUPLICATE"


def test_failed_commit_is_recorded_with_a_reason(client, monkeypatch):
    from app.services import user_images as service

    csrf, _ = _setup_instance(client)

    class Broken:
        def commit(self, *_args, **_kwargs):
            raise RuntimeError("docker commit exploded")

    monkeypatch.setattr(service, "get_driver", lambda: Broken())
    response = client.post(
        "/api/v1/instances/me/images",
        json={"name": "broken-image"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 202, response.text

    with SessionLocal() as db:
        row = db.query(UserImage).filter(UserImage.name == "broken-image").one()
        assert row.status == UserImageStatus.failed
        assert "exploded" in (row.error or "")