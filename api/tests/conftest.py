from __future__ import annotations

import os
import shutil

DB_PATH = f"./test_console_{os.getpid()}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
os.environ["REDIS_FAKE"] = "1"
os.environ.setdefault("SESSION_SECRET", "test-session-secret")
os.environ.setdefault("BOOTSTRAP_ADMIN_PASSWORD", "admin12345")
os.environ["LOGIN_RATE_PER_MINUTE"] = "20"
os.environ["ENV"] = "test"
os.environ["DOCKER_FAKE"] = "1"
os.environ["CADDY_ENABLED"] = "0"
os.environ["JOBS_INLINE"] = "1"
os.environ["DATA_ROOT"] = "./test_data"
os.environ["HOST_DATA_ROOT"] = "./test_data"
os.environ["PUBLIC_SCHEME"] = "http"

import pytest
from fastapi.testclient import TestClient

from app.drivers import reset_driver
from app.main import app


@pytest.fixture(scope="module")
def client():
    shutil.rmtree("test_data", ignore_errors=True)
    reset_driver()
    with TestClient(app) as test_client:
        yield test_client
    reset_driver()
    shutil.rmtree("test_data", ignore_errors=True)
    from app.db import engine

    engine.dispose()
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)


def login(client: TestClient, username: str = "admin", password: str = "admin12345") -> str:
    response = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password, "remember": True}
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["csrf_token"]
