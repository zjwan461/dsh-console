from __future__ import annotations

import pytest

from app.db import SessionLocal
from app.drivers import get_driver, reset_driver
from app.models import DshImage, ImageStatus, Instance, InstanceStatus, User
from app.services import reconcile


def _admin_csrf(client) -> str:
    return client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin12345", "remember": True},
    ).json()["data"]["csrf_token"]


def _make_instance(client) -> Instance:
    csrf = _admin_csrf(client)
    with SessionLocal() as db:
        user = db.query(User).filter(User.username == "admin").one()
        existing = db.query(Instance).filter(Instance.user_id == user.id).first()
        if existing is not None:
            return existing
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
    response = client.post("/api/v1/instances/me", json={}, headers={"X-CSRF-Token": csrf})
    assert response.status_code in (200, 202), response.text
    with SessionLocal() as db:
        user = db.query(User).filter(User.username == "admin").one()
        return db.query(Instance).filter(Instance.user_id == user.id).one()


def test_reconcile_recovers_an_instance_that_came_up_after_the_timeout(client):
    """A slow first boot is not a failure.

    `wait_healthy` gives up while dsh is still composing its profile; the
    container then becomes healthy. Without this the console kept showing
    「异常」 for a perfectly working instance until someone restarted it.
    """
    instance = _make_instance(client)
    driver = get_driver()
    with SessionLocal() as db:
        row = db.get(Instance, instance.id)
        row.status = InstanceStatus.error
        row.error = "container did not become healthy (status=running)"
        row.health = "unhealthy"
        container_id = row.container_id
        db.commit()
    assert container_id
    driver.start(container_id)

    with SessionLocal() as db:
        result = reconcile.reconcile(db)
        assert result["recovered"] >= 1
        db.expire_all()
        row = db.get(Instance, instance.id)
        assert row.status == InstanceStatus.running
        assert row.error is None
        assert row.health == "healthy"


def test_reconcile_leaves_a_genuinely_broken_instance_alone(client):
    instance = _make_instance(client)
    driver = get_driver()
    with SessionLocal() as db:
        row = db.get(Instance, instance.id)
        row.status = InstanceStatus.error
        row.error = "container exited"
        container_id = row.container_id
        db.commit()
    driver.stop(container_id)

    with SessionLocal() as db:
        reconcile.reconcile(db)
        db.expire_all()
        row = db.get(Instance, instance.id)
        assert row.status == InstanceStatus.error
        assert row.error == "container exited"


def test_health_timeout_is_configurable():
    from app.config import settings

    assert settings.instance_health_timeout >= 90


@pytest.fixture(autouse=True)
def _reset():
    yield
    reset_driver()