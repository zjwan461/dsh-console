from __future__ import annotations

import json
import logging

import httpx
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Instance, InstanceStatus
from .settings import get_setting

log = logging.getLogger("dsh.caddy")


class CaddyError(RuntimeError):
    pass


def _console_routes(console_host: str) -> dict:
    return {
        "match": [{"host": [console_host]}],
        "handle": [
            {
                "handler": "subroute",
                "routes": [
                    {
                        "match": [{"path": ["/api/*", "/ws/*"]}],
                        "handle": [
                            {
                                "handler": "reverse_proxy",
                                "upstreams": [{"dial": "console-api:8000"}],
                                "flush_interval": -1,
                            }
                        ],
                        "terminal": True,
                    },
                    {
                        "match": [{"path": ["/assets/*", "/favicon.ico"]}],
                        "handle": [{"handler": "file_server", "root": "/srv/console-web"}],
                    },
                    {
                        "handle": [
                            {"handler": "rewrite", "uri": "/index.html"},
                            {"handler": "file_server", "root": "/srv/console-web"},
                        ]
                    },
                ],
            }
        ],
        "terminal": True,
    }


def _instance_route(instance: Instance, base_domain: str) -> dict:
    host = f"{instance.subdomain}.{base_domain}"
    if instance.status == InstanceStatus.running:
        handle = [
            {
                "handler": "reverse_proxy",
                "upstreams": [{"dial": f"dsh-u-{instance.user_id}:3080"}],
                "flush_interval": -1,
            }
        ]
    else:
        # Anything not running lands on the wake page. The page itself never
        # hands out the DSH token; it only starts the container and redirects.
        handle = [
            {"handler": "rewrite", "uri": f"/w/{instance.wake_key}"},
            {
                "handler": "reverse_proxy",
                "upstreams": [{"dial": "console-api:8000"}],
                "flush_interval": -1,
            },
        ]
    return {
        "match": [{"host": [host]}],
        "handle": handle,
        "terminal": True,
    }


def build_config(db: Session, base_domain: str | None = None) -> dict:
    domain = base_domain or str(get_setting(db, "base_domain", settings.base_domain))
    console_host = f"console.{domain}"
    instances = (
        db.query(Instance)
        .filter(Instance.status != InstanceStatus.deleted)
        .order_by(Instance.id)
        .all()
    )
    routes = [_console_routes(console_host)]
    routes.extend(_instance_route(item, domain) for item in instances)
    routes.append(
        {
            "match": [{"host": [f"*.{domain}"]}],
            "handle": [{"handler": "static_response", "status_code": 404, "body": "Not Found"}],
            "terminal": True,
        }
    )
    return {
        "admin": {"listen": "0.0.0.0:2019"},
        "apps": {
            "http": {
                "servers": {
                    "srv0": {
                        "listen": [":443"],
                        "routes": routes,
                    }
                }
            },
            "tls": {
                "automation": {
                    "policies": [
                        {
                            "subjects": [f"*.{domain}", console_host],
                            "issuers": [{"module": "internal"}],
                        }
                    ]
                }
            },
        },
    }


def apply(db: Session) -> None:
    if not settings.caddy_enabled:
        log.debug("caddy disabled; generated config not applied")
        return
    config = build_config(db)
    try:
        response = httpx.post(
            f"{settings.caddy_admin_url.rstrip('/')}/load",
            content=json.dumps(config),
            headers={"Content-Type": "application/json"},
            timeout=5.0,
        )
        response.raise_for_status()
    except Exception as exc:
        raise CaddyError(f"caddy config load failed: {exc}") from exc
    log.info(
        "caddy config applied (%d routes)", len(config["apps"]["http"]["servers"]["srv0"]["routes"])
    )
