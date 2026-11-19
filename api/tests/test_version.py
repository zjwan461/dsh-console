from __future__ import annotations

from app.version import __version__


def test_health_reports_the_package_version(client):
    """A release is only trustworthy if the running build reports its own tag."""
    from conftest import login

    csrf = login(client)
    response = client.get("/api/v1/system/health", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert response.json()["data"]["version"] == __version__


def test_openapi_reports_the_same_version(client):
    response = client.get("/api/openapi.json")
    assert response.status_code == 200
    assert response.json()["info"]["version"] == __version__


def test_version_looks_like_semver():
    parts = __version__.split(".")
    assert len(parts) == 3
    assert all(part.isdigit() for part in parts)