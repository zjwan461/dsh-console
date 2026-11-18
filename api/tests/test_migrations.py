from __future__ import annotations

import os
import sqlite3
import subprocess
import sys

from fastapi.testclient import TestClient

from app.migrations import current_revision, ensure_schema, head_revision, schema_summary

API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_test_database_is_at_head(client: TestClient):
    """Starting the API must have migrated the schema, not just created tables."""
    summary = schema_summary()
    assert summary["head"]
    assert summary["current"] == summary["head"]
    assert current_revision() == head_revision()


def test_ensure_schema_is_idempotent(client: TestClient):
    before = current_revision()
    ensure_schema()
    ensure_schema()
    assert current_revision() == before == head_revision()


def test_doctor_reports_migration_status(client: TestClient):
    from app.doctor import PASS, run_doctor

    checks = {check.name: check for check in run_doctor()}
    assert checks["迁移版本"].level == PASS
    assert checks["迁移版本"].detail.startswith(head_revision())


def _run_ensure_schema(db_path: str) -> None:
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{db_path}",
        "ENV": "test",
        "DATA_ROOT": os.path.join(os.path.dirname(db_path), "data"),
        "HOST_DATA_ROOT": os.path.join(os.path.dirname(db_path), "data"),
        "REDIS_FAKE": "1",
        "DOCKER_FAKE": "1",
        "CADDY_ENABLED": "0",
    }
    subprocess.run(
        [sys.executable, "-c", "from app.migrations import ensure_schema; ensure_schema()"],
        cwd=API_DIR,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_legacy_stamp_is_healed_with_missing_columns(tmp_path):
    """A dev DB stamped at head but missing an added column must be repaired.

    This is the exact shape the old `create_all` path produced: no
    `alembic_version`, so the first boot stamps head -- but stamping does not add
    columns, and `provider_credentials.model` would then be missing.
    """
    db_path = tmp_path / "legacy.db"
    _run_ensure_schema(str(db_path))

    with sqlite3.connect(db_path) as conn:
        conn.execute("ALTER TABLE provider_credentials DROP COLUMN model")
        conn.execute("DROP TABLE alembic_version")
        conn.commit()

    _run_ensure_schema(str(db_path))

    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(provider_credentials)")}
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    assert "model" in columns
    assert version == head_revision()
