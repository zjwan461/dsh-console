from __future__ import annotations

import json

import httpx
import pytest

from app.services.registry import (
    RegistryClient,
    RegistryError,
    parse_repository,
    parse_timestamp,
    split_tag,
    version_key,
)

REPO = "ghcr.io/zjwan461/dsh-docker"
INDEX = {
    "mediaType": "application/vnd.oci.image.index.v1+json",
    "manifests": [
        {"digest": "sha256:arm", "platform": {"os": "linux", "architecture": "arm64"}},
        {"digest": "sha256:amd", "platform": {"os": "linux", "architecture": "amd64"}},
    ],
}
MANIFEST = {
    "mediaType": "application/vnd.oci.image.manifest.v1+json",
    "config": {"digest": "sha256:cfg"},
    "layers": [{"size": 1000}, {"size": 2000}],
}
CONFIG = {
    "created": "2026-11-01T10:00:00.000000000Z",
    "config": {"Labels": {"dsh.version": "0.2.1-rc.5"}},
}


def build_transport(tags: list[str], *, fail: bool = False) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if fail:
            raise httpx.ConnectError("refused", request=request)
        if url.endswith("/v2/") and "token" not in url:
            return httpx.Response(
                401,
                headers={
                    "www-authenticate": 'Bearer realm="https://ghcr.io/token",service="ghcr.io"'
                },
            )
        if "/token" in url:
            return httpx.Response(200, json={"token": "test-token"})
        if url.endswith("/tags/list?n=200"):
            return httpx.Response(200, json={"name": "dsh-docker", "tags": tags})
        if "/manifests/" in url:
            tag = url.rsplit("/", 1)[-1]
            if tag == "missing":
                return httpx.Response(404, json={"errors": [{"code": "MANIFEST_UNKNOWN"}]})
            if tag in {"latest", "0.2.1-rc.5", "0.2.0-rc.1-slim"}:
                if tag == "latest":
                    return httpx.Response(
                        200, json=MANIFEST, headers={"docker-content-digest": "sha256:latest"}
                    )
                if tag == "0.2.1-rc.5":
                    return httpx.Response(
                        200, json=INDEX, headers={"docker-content-digest": "sha256:idx-021"}
                    )
                return httpx.Response(
                    200, json=MANIFEST, headers={"docker-content-digest": "sha256:slim-020"}
                )
            if tag == "sha256:amd":
                return httpx.Response(200, json=MANIFEST)
            return httpx.Response(200, json=MANIFEST, headers={"docker-content-digest": "sha256:x"})
        if "/blobs/" in url:
            return httpx.Response(200, json=CONFIG)
        return httpx.Response(200, json={})

    return httpx.MockTransport(handler)


def make_client(tags: list[str], **kwargs) -> RegistryClient:
    return RegistryClient(
        REPO,
        http=httpx.Client(transport=build_transport(tags, **kwargs)),
        username="",
        password="",
    )


def test_parse_repository_variants():
    assert parse_repository("ghcr.io/o/r") == ("ghcr.io", "o/r")
    assert parse_repository("https://ghcr.io/o/r/") == ("ghcr.io", "o/r")
    assert parse_repository("ghcr.io/o/r:0.2.1") == ("ghcr.io", "o/r")
    assert parse_repository("ghcr.io/o/r@sha256:abc") == ("ghcr.io", "o/r")
    with pytest.raises(RegistryError):
        parse_repository("nonsense")


def test_split_tag_and_ordering():
    assert split_tag("0.2.1-rc.5") == ("0.2.1-rc.5", "full")
    assert split_tag("v0.2.1") == ("0.2.1", "full")
    assert split_tag("0.2.0-slim") == ("0.2.0", "slim")
    assert split_tag("latest") is None
    assert split_tag("sha-abc1234") is None
    assert version_key("0.2.1-rc.5") < version_key("0.2.1")
    assert version_key("0.2.1") < version_key("0.3.0")


def test_parse_timestamp_handles_nanoseconds():
    """BuildKit emits 9 fractional digits; fromisoformat only accepts 6."""
    assert parse_timestamp("2026-11-17T05:59:28.166507199Z").isoformat() == (
        "2026-11-17T05:59:28.166507"
    )
    assert parse_timestamp("2026-11-17T05:59:28Z").year == 2026
    assert parse_timestamp("2026-11-17T05:59:28.123+00:00").microsecond == 123000
    assert parse_timestamp(None) is None
    assert parse_timestamp("not-a-date") is None


def test_candidates_filter_and_enrich():
    client = make_client(["latest", "sha-abc1234", "0.2.1-rc.5", "0.2.0-rc.1-slim", "missing"])
    items = client.candidates()
    tags = [item.tag for item in items]
    # latest and sha-* are not versions; `missing` 404s but must not abort the scan.
    assert "latest" not in tags
    assert "sha-abc1234" not in tags
    assert "missing" not in tags
    assert tags == ["0.2.1-rc.5", "0.2.0-rc.1-slim"]

    full = items[0]
    assert full.digest == "sha256:idx-021"  # index digest is the stable identity
    assert full.platform_digest == "sha256:amd"
    assert full.size_bytes == 3000
    assert full.created_at is not None and full.created_at.year == 2026
    assert full.labels["dsh.version"] == "0.2.1-rc.5"
    assert items[1].variant == "slim"


def test_registry_failure_is_typed():
    client = make_client([], fail=True)
    with pytest.raises(RegistryError):
        client.list_tags()


def test_catalog_marks_imported_and_reports_update(client, monkeypatch):
    from app.services import image_catalog

    db_session = image_catalog.__dict__  # keep reference for clarity
    assert "catalog" in db_session

    monkeypatch.setattr(
        image_catalog, "_client", lambda db: make_client(["latest", "0.2.1-rc.5", "0.1.5"])
    )

    from app.db import SessionLocal

    # Register one published tag under its version but with a local ref, to prove
    # matching works even when the reference differs.
    admin_csrf = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin12345", "remember": True},
    ).json()["data"]["csrf_token"]
    client.post(
        "/api/v1/admin/images",
        json={"image_ref": "dsh:0.2.1-rc.5", "version": "0.2.1-rc.5", "is_default": True},
        headers={"X-CSRF-Token": admin_csrf},
    )

    response = client.get("/api/v1/admin/images/remote?refresh=true")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["repository"].endswith("dsh-docker")
    assert data["latest_version"] == "0.2.1-rc.5"
    by_tag = {item["tag"]: item for item in data["items"]}
    assert by_tag["0.2.1-rc.5"]["imported"] is True
    assert by_tag["0.2.1-rc.5"]["matched_by"] == "version"
    assert by_tag["0.1.5"]["imported"] is False
    # Same version as the default -> nothing to upgrade to.
    assert data["update_available"] is False

    with SessionLocal() as db:
        from app.services.settings import get_setting

        stored = get_setting(db, image_catalog.STATE_KEY)
    assert stored["checked_at"]


def test_catalog_survives_registry_outage(client, monkeypatch):
    """A registry outage must keep serving the last good snapshot, with a flag."""
    from app.services import image_catalog

    # A previous test already stored a snapshot, so we expect it to survive.
    monkeypatch.setattr(image_catalog, "_client", lambda db: make_client([], fail=True))
    response = client.get("/api/v1/admin/images/remote?refresh=true")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["error"]
    assert data["stale"] is True
    # The cached catalogue is still there, so the page does not go blank.
    assert data["items"]
    assert data["checked_at"]


def test_import_remote_tag(client, monkeypatch):
    from app.services import image_catalog

    monkeypatch.setattr(image_catalog, "_client", lambda db: make_client(["latest", "0.3.0"]))
    admin_csrf = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin12345", "remember": True},
    ).json()["data"]["csrf_token"]

    response = client.post(
        "/api/v1/admin/images/remote/import",
        json={"tag": "0.3.0", "is_default": False},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert response.status_code == 201, response.text
    image = response.json()["data"]
    assert image["image_ref"] == f"{REPO}:0.3.0"
    assert image["version"] == "0.3.0"
    assert image["source"] == "pull"

    # Importing the same tag twice is rejected.
    again = client.post(
        "/api/v1/admin/images/remote/import",
        json={"tag": "0.3.0"},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert again.status_code == 409


def test_import_tar_upload(client):
    admin_csrf = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin12345", "remember": True},
    ).json()["data"]["csrf_token"]

    payload = b"not-a-real-tar" * 100
    response = client.post(
        "/api/v1/admin/images/upload",
        files={"file": ("dsh-0.4.0.tar", payload, "application/x-tar")},
        data={"version": "0.4.0", "changelog": "offline import"},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert response.status_code == 201, response.text
    image = response.json()["data"]
    # FakeDriver reports the file name as the loaded reference.
    assert image["version"] == "0.4.0"
    assert image["source"] == "upload"
    assert image["changelog"] == "offline import"


def test_upload_rejects_oversized_tar(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "image_upload_max_mb", 0)
    admin_csrf = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin12345", "remember": True},
    ).json()["data"]["csrf_token"]
    response = client.post(
        "/api/v1/admin/images/upload",
        files={"file": ("big.tar", b"x" * 2048, "application/x-tar")},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert response.status_code == 413


def test_state_json_is_serialisable(client, monkeypatch):
    """The catalogue cache lives in a JSON column; make sure it round-trips."""
    from app.services import image_catalog

    monkeypatch.setattr(image_catalog, "_client", lambda db: make_client(["0.2.1-rc.5"]))
    assert client.get("/api/v1/admin/images/remote?refresh=true").status_code == 200
    from app.db import SessionLocal
    from app.services.settings import get_setting

    with SessionLocal() as db:
        stored = get_setting(db, image_catalog.STATE_KEY)
    assert json.loads(json.dumps(stored)) == stored