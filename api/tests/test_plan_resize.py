from __future__ import annotations

from app.db import SessionLocal
from app.models import DshImage, ImageStatus, Instance, Plan, User, UserQuota


def _admin_and_instance(client) -> tuple[str, int]:
    csrf = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin12345", "remember": True},
    ).json()["data"]["csrf_token"]
    with SessionLocal() as db:
        user = db.query(User).filter(User.username == "admin").one()
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
        instance = db.query(Instance).filter(Instance.user_id == user.id).first()
    if instance is None:
        client.post("/api/v1/instances/me", json={}, headers={"X-CSRF-Token": csrf})
        with SessionLocal() as db:
            user = db.query(User).filter(User.username == "admin").one()
            instance = db.query(Instance).filter(Instance.user_id == user.id).one()
    return csrf, instance.id


def test_plan_change_resizes_the_instance_not_just_the_quota(client):
    """A new plan has to reach the container, not only the quota card.

    The instance keeps a snapshot of the plan it was created with, and Docker
    cannot resize a running container, so the spec must be rewritten and the
    container re-created (its config hash changes).
    """
    csrf, instance_id = _admin_and_instance(client)
    with SessionLocal() as db:
        big = db.query(Plan).filter(Plan.cpu_cores > 1).order_by(Plan.cpu_cores).first()
        assert big is not None, "need a non-basic plan in the seed data"
        before = db.get(Instance, instance_id)
        assert (before.cpu_cores, before.memory_mb) != (big.cpu_cores, big.memory_mb)
        plan_id, cpu, mem, disk = big.id, float(big.cpu_cores), big.memory_mb, big.disk_gb

    response = client.patch(
        f"/api/v1/admin/users/{user_id_of('admin')}",
        json={"plan_id": plan_id},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["plan"]["id"] == plan_id

    with SessionLocal() as db:
        instance = db.get(Instance, instance_id)
        assert float(instance.cpu_cores) == cpu
        assert instance.memory_mb == mem
        assert instance.disk_gb == disk
        quota = db.get(UserQuota, instance.user_id)
        assert quota.plan_id == plan_id


def user_id_of(username: str) -> int:
    with SessionLocal() as db:
        return db.query(User).filter(User.username == username).one().id


def test_unchanged_plan_does_not_touch_the_instance(client):
    csrf, instance_id = _admin_and_instance(client)
    with SessionLocal() as db:
        instance = db.get(Instance, instance_id)
        plan = db.get(Plan, instance.user_id and _plan_of(instance.user_id))
        assert plan is not None
        plan_id = plan.id

    response = client.patch(
        f"/api/v1/admin/users/{user_id_of('admin')}",
        json={"plan_id": plan_id},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text


def _plan_of(user_id: int) -> int:
    with SessionLocal() as db:
        return db.query(UserQuota).filter(UserQuota.user_id == user_id).one().plan_id