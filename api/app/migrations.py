"""Schema bootstrapping: always converge the database to `head` on startup.

The console is a long-lived service that gets upgraded in place, so every boot
must answer one question: "is the database at the revision this code expects?".

Rules
-----
* Fresh database           -> `alembic upgrade head` (no `create_all`, so the
                              migration chain is the single source of truth).
* Managed database          -> upgrade only when the recorded revision differs.
* Legacy dev database       -> tables exist but no `alembic_version` (created by
                              the old `create_all` path): create missing tables,
                              stamp head, and warn that added columns need a
                              fresh dev database.

`GET_LOCK` serialises concurrent API workers on MySQL; SQLite (dev/tests) uses a
process-local lock, which is enough because it only ever backs a single process.
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.schema import CreateColumn

from alembic import command

from .config import settings
from .db import Base, engine

log = logging.getLogger("dsh.migrate")

API_DIR = Path(__file__).resolve().parent.parent
_LOCAL_LOCK = threading.Lock()


def alembic_config() -> Config:
    config = Config(str(API_DIR / "alembic.ini"))
    # Resolve relative to this file, not to the process cwd: the same code runs
    # from `api/` in development and `/app` inside the container.
    config.set_main_option("script_location", str(API_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    return config


def head_revision() -> str:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def current_revision() -> str | None:
    with engine.connect() as conn:
        if "alembic_version" not in inspect(conn).get_table_names():
            return None
        row = conn.exec_driver_sql("SELECT version_num FROM alembic_version").fetchone()
    return row[0] if row else None


@contextmanager
def _lock():
    """Serialise migrations across processes (MySQL) or threads (SQLite)."""
    if engine.dialect.name == "mysql":
        with engine.connect() as conn:
            got = conn.exec_driver_sql("SELECT GET_LOCK('dsh_console_migrate', 60)").scalar()
            try:
                if got != 1:
                    raise RuntimeError("could not acquire migration lock within 60s")
                yield
            finally:
                conn.exec_driver_sql("SELECT RELEASE_LOCK('dsh_console_migrate')")
        return
    with _LOCAL_LOCK:
        yield


def _missing_columns() -> list[tuple[str, object]]:
    """Columns declared on the models but absent from the database."""
    inspector = inspect(engine)
    missing: list[tuple[str, object]] = []
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        existing = {column["name"] for column in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name not in existing:
                missing.append((table.name, column))
    return missing


def _heal_missing_columns() -> None:
    """Add additive columns that the migration chain could not reach.

    Covers databases created by the old `create_all` path (no `alembic_version`):
    stamping only records the revision, it does not add columns. Nullable/defaulted
    columns are safe to `ALTER TABLE ADD COLUMN`; anything stricter fails loudly.
    """
    missing = _missing_columns()
    if not missing:
        return
    quote = engine.dialect.identifier_preparer.quote
    with engine.begin() as conn:
        for table_name, column in missing:
            if not column.nullable and column.default is None and column.server_default is None:
                raise RuntimeError(
                    f"schema drift: {table_name}.{column.name} is NOT NULL without a default; "
                    "recreate the database or write a migration"
                )
            ddl = CreateColumn(column).compile(dialect=engine.dialect)
            conn.exec_driver_sql(f"ALTER TABLE {quote(table_name)} ADD COLUMN {ddl}")
            log.warning("added missing column %s.%s", table_name, column.name)


def ensure_schema() -> None:
    """Bring the database to `head` and repair additive drift; safe on every start."""
    config = alembic_config()
    with engine.connect() as conn:
        tables = set(inspect(conn).get_table_names())

    if not tables:
        log.info("empty database: applying migrations to head")
        with _lock():
            command.upgrade(config, "head")
        log.info("schema ready at %s", current_revision())
        return

    if "alembic_version" not in tables:
        # A database created by the old `create_all` path (or a hand-made one).
        log.warning(
            "database has tables but no alembic_version; creating missing tables, "
            "adding missing columns, then stamping head"
        )
        Base.metadata.create_all(engine)
        with _lock():
            command.stamp(config, "head")
        _heal_missing_columns()
        return

    head = head_revision()
    current = current_revision()
    if current != head:
        log.info("schema upgrade required: %s -> %s", current or "<none>", head)
        with _lock():
            command.upgrade(config, "head")
        log.info("schema upgraded to %s", current_revision())
    else:
        log.info("schema already at head (%s)", head)

    # Even at head a legacy stamp can leave additive columns missing.
    _heal_missing_columns()


def schema_summary() -> dict[str, str | None]:
    """Used by `app.cli doctor` and tests."""
    try:
        return {"current": current_revision(), "head": head_revision()}
    except Exception as exc:  # noqa: BLE001
        return {"current": None, "head": None, "error": str(exc)}
