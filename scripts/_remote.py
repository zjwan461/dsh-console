"""Shared, host-agnostic configuration for the remote verification scripts.

Nothing host-specific belongs in the repository: every value comes from the
environment so that these scripts can be committed and shared.

    export DSH_REMOTE_DOCKER=http://10.0.0.10:2375     # Docker daemon (no TLS)
    export DSH_CONSOLE_URL=http://10.0.0.10:38080      # console over plain HTTP
    export DSH_API_URL=http://10.0.0.10:38000/api/v1   # API base
    export DSH_ADMIN_USER=admin
    export DSH_ADMIN_PASSWORD=*********
    export DSH_HOST_DATA_ROOT=/opt/dsh-console/data    # host-side data dir
    export DSH_MYSQL_DSN='mysql+pymysql://u:p@host:3306/dsh_console?charset=utf8mb4'
    export DSH_BASE_DOMAIN=dsh.example.com             # placeholder domain

A git-ignored `scripts/.remote.env` is loaded automatically when present, so the
values can also live in one file that never reaches git.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ENV_FILE = Path(__file__).with_name(".remote.env")


def _load_env_file() -> None:
    if not _ENV_FILE.exists():
        return
    for raw in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_env_file()


def require(name: str, example: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        print(
            f"missing environment variable {name}\n"
            f"  example: export {name}={example}\n"
            f"  (or put it in scripts/.remote.env, which is git-ignored)",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return value.rstrip("/")


def optional(name: str, default: str) -> str:
    return os.environ.get(name, "").strip() or default


DOCKER = require("DSH_REMOTE_DOCKER", "http://10.0.0.10:2375")
CONSOLE = require("DSH_CONSOLE_URL", "http://10.0.0.10:38080")
API = require("DSH_API_URL", "http://10.0.0.10:38000/api/v1")
ADMIN = (
    optional("DSH_ADMIN_USER", "admin"),
    require("DSH_ADMIN_PASSWORD", "********"),
)

DATA_ROOT = optional("DSH_HOST_DATA_ROOT", "/opt/dsh-console/data")
BASE_DOMAIN = optional("DSH_BASE_DOMAIN", "dsh.example.com")
CONSOLE_HOST = f"console.{BASE_DOMAIN}"
MYSQL_DSN = os.environ.get("DSH_MYSQL_DSN", "").strip()
REDIS_URL = optional("DSH_REDIS_URL", "redis://redis:6379/0")
API_IMAGE = optional("DSH_API_IMAGE", "dsh-console-api:dev")
WEB_IMAGE = optional("DSH_WEB_IMAGE", "dsh-console-web:dev")
NET_CONSOLE = optional("DSH_NET_CONSOLE", "dsh-console")
NET_EDGE = optional("DSH_NET_EDGE", "dsh-edge")
HTTP_PORT = optional("DSH_HTTP_PORT", "38080")
API_PORT = optional("DSH_API_PORT", "38000")