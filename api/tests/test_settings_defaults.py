from __future__ import annotations

from app.config import settings
from app.services.settings import adopt_env_defaults, default_settings


def test_defaults_follow_the_environment(monkeypatch):
    """`CONSOLE_BASE_DOMAIN` must reach the defaults; it used to be hardcoded.

    When it was, a fresh database was seeded with `dsh.example.com`, Caddy issued
    certificates for that name, and the configured domain never worked.
    """
    monkeypatch.setattr(settings, "base_domain", "dsh.gen-ai.website")
    monkeypatch.setattr(settings, "console_name", "测试舱")
    defaults = default_settings()
    assert defaults["base_domain"] == "dsh.gen-ai.website"
    assert defaults["console_name"] == "测试舱"


def test_adopt_env_defaults_replaces_the_legacy_seed(client, monkeypatch):
    from app.db import SessionLocal
    from app.models import Setting

    monkeypatch.setattr(settings, "base_domain", "dsh.gen-ai.website")
    with SessionLocal() as db:
        row = db.get(Setting, "base_domain")
        if row is None:
            db.add(Setting(k="base_domain", v="dsh.example.com"))
        else:
            row.v = "dsh.example.com"
        db.commit()

        assert "base_domain" in adopt_env_defaults(db)
        db.expire_all()
        assert db.get(Setting, "base_domain").v == "dsh.gen-ai.website"


def test_adopt_env_defaults_keeps_a_deliberate_choice(client, monkeypatch):
    """An admin who typed a custom domain must not be overridden by the env."""
    from app.db import SessionLocal
    from app.models import Setting

    monkeypatch.setattr(settings, "base_domain", "dsh.gen-ai.website")
    with SessionLocal() as db:
        row = db.get(Setting, "base_domain")
        if row is None:
            db.add(Setting(k="base_domain", v="custom.internal"))
        else:
            row.v = "custom.internal"
        db.commit()

        assert "base_domain" not in adopt_env_defaults(db)
        db.expire_all()
        assert db.get(Setting, "base_domain").v == "custom.internal"