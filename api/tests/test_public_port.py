from __future__ import annotations

from app.config import settings


def _with(scheme: str, port: int | None) -> str:
    original = (settings.public_scheme, settings.public_port)
    try:
        settings.public_scheme = scheme
        settings.public_port = port
        from app.services.instances import public_authority

        return public_authority("dsh.example.com")
    finally:
        settings.public_scheme, settings.public_port = original


def test_no_port_configured_is_empty():
    assert _with("https", None) == ""
    assert _with("http", None) == ""


def test_default_ports_are_omitted():
    assert _with("https", 443) == ""
    assert _with("http", 80) == ""


def test_non_default_port_is_carried():
    # The browser sends `Host: sub.domain:38080`; DSH's trusted-host fence needs
    # the same authority, and /go has to redirect to a reachable URL.
    assert _with("http", 38080) == ":38080"
    assert _with("https", 8443) == ":8443"