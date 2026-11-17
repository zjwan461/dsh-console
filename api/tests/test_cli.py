from __future__ import annotations

from fastapi.testclient import TestClient

from app.cli import create_admin, list_users, reset_password
from app.db import SessionLocal
from app.models import User
from app.security import verify_password


def test_list_users_includes_bootstrap_admin(client: TestClient):
    with SessionLocal() as db:
        usernames = [user.username for user in list_users(db)]
    assert "admin" in usernames


def test_reset_password_generates_and_revokes_sessions(client: TestClient):
    # Log in so there is a live session to revoke.
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin12345", "remember": True},
    )
    assert login.status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 200

    with SessionLocal() as db:
        user, generated = reset_password(db, "admin")
        assert generated, "a password should be generated when none is supplied"
        assert verify_password(user.password_hash, generated)
        assert user.must_change_password is True

    # The old session is gone.
    assert client.get("/api/v1/auth/me").status_code == 401

    # The generated password works, and it must be changed.
    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": generated})
    assert login.status_code == 200, login.text
    assert login.json()["data"]["user"]["must_change_password"] is True

    # Put the well-known password back for the remaining tests.
    with SessionLocal() as db:
        user, _ = reset_password(db, "admin", "admin12345")
        assert verify_password(user.password_hash, "admin12345")


def test_create_admin(client: TestClient):
    with SessionLocal() as db:
        user, generated = create_admin(db, "ops", "opspass123", "运维")
        assert user.role.value == "admin"
        assert generated == ""
        assert verify_password(user.password_hash, "opspass123")
        assert "ops" in [row.username for row in list_users(db)]

    login = client.post("/api/v1/auth/login", json={"username": "ops", "password": "opspass123"})
    assert login.status_code == 200
    assert login.json()["data"]["user"]["role"] == "admin"

    # A duplicate username is rejected rather than silently overwritten.
    with SessionLocal() as db:
        try:
            create_admin(db, "ops", "another123")
        except SystemExit as exc:
            assert "already exists" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("duplicate admin should be rejected")


def test_cli_rejects_short_and_unknown(client: TestClient):
    with SessionLocal() as db:
        try:
            reset_password(db, "admin", "short")
        except SystemExit as exc:
            assert "8 characters" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("short password should be rejected")

        try:
            reset_password(db, "nobody")
        except SystemExit as exc:
            assert "no such user" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("unknown user should be rejected")


def test_cli_main_runs(client: TestClient, capsys):
    from app.cli import main

    assert main(["list-users"]) == 0
    out = capsys.readouterr().out
    assert "username" in out
    assert "admin" in out

    assert main(["show-config"]) == 0
    out = capsys.readouterr().out
    assert "DATABASE_URL" in out

    with SessionLocal() as db:
        user = db.query(User).filter(User.username == "cli-user").first()
    assert user is None
    assert main(["create-admin", "cli-user", "--password", "clipass123"]) == 0
    assert "cli-user" in capsys.readouterr().out
