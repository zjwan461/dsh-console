from __future__ import annotations

import pytest

from app.config import Settings


@pytest.mark.parametrize("name", ["CONSOLE_BASE_DOMAIN", "BASE_DOMAIN"])
def test_base_domain_env_names(monkeypatch, name: str):
    """`.env.example` and compose both set CONSOLE_BASE_DOMAIN.

    The field is `base_domain`, so pydantic would only have looked at
    `BASE_DOMAIN` — the documented variable was ignored and Caddy kept issuing
    certificates for the built-in default domain.
    """
    monkeypatch.delenv("CONSOLE_BASE_DOMAIN", raising=False)
    monkeypatch.delenv("BASE_DOMAIN", raising=False)
    monkeypatch.setenv(name, "dsh.gen-ai.website")
    assert Settings().base_domain == "dsh.gen-ai.website"


def test_base_domain_falls_back_to_the_default(monkeypatch):
    monkeypatch.delenv("CONSOLE_BASE_DOMAIN", raising=False)
    monkeypatch.delenv("BASE_DOMAIN", raising=False)
    assert Settings().base_domain == "dsh.example.com"


def test_base_domain_still_accepts_the_field_name():
    assert Settings(base_domain="x.internal").base_domain == "x.internal"