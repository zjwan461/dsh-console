from __future__ import annotations

import pytest

from app.config import Settings


@pytest.mark.parametrize("blank", ["", " ", "\t", "  \n"])
def test_blank_public_port_means_unset(blank: str):
    """docker compose renders `${PUBLIC_PORT:-}` as an empty string.

    Parsing that as an int crashed the API on boot — before it could log
    anything useful — so a blank value must mean "not configured".
    """
    assert Settings(public_port=blank).public_port is None


def test_explicit_public_port_is_kept():
    assert Settings(public_port="38080").public_port == 38080
    assert Settings(public_port=8443).public_port == 8443


def test_default_public_port_is_none():
    assert Settings(public_port=None).public_port is None


@pytest.mark.parametrize("blank", ["", " "])
def test_blank_caddy_listen_port_means_unset(blank: str):
    assert Settings(caddy_listen_port=blank).caddy_listen_port is None