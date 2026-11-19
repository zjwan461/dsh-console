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
                        # /go (launch ticket) and /w (wake page) are served by the
                        # API, not by the SPA. Missing them here made the console
                        # serve index.html for a launch ticket (200 instead of the
                        # 302 that injects the DSH token).
                        "match": [{"path": ["/api/*", "/ws/*", "/go/*", "/w/*"]}],
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
        # /w/* must bypass the rewrite, otherwise the page's own status polling
        # would be rewritten back to the HTML page.
        api_proxy = {
            "handler": "reverse_proxy",
            "upstreams": [{"dial": "console-api:8000"}],
            "flush_interval": -1,
        }
        handle = [
            {
                "handler": "subroute",
                "routes": [
                    {
                        "match": [{"path": ["/w/*"]}],
                        "handle": [api_proxy],
                        "terminal": True,
                    },
                    {
                        "handle": [
                            {"handler": "rewrite", "uri": f"/w/{instance.wake_key}"},
                            api_proxy,
                        ],
                        "terminal": True,
                    },
                ],
            }
        ]
    return {
        "match": [{"host": [host]}],
        "handle": handle,
        "terminal": True,
    }


def _log_sink_reachable(host: str, port: int) -> bool:
    """Can Caddy actually open the access-log socket right now?

    Caddy dials a `net` log writer while *loading* the config, so an unreachable
    sink fails the whole `/load` — routes included. On a cold `docker compose up`
    the worker usually does not exist yet, which used to make the API log a
    traceback and leave Caddy in its bootstrap state until the first reconcile.
    """
    import socket

    try:
        socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    return True


def build_config(db: Session, base_domain: str | None = None) -> dict:
    domain = base_domain or str(get_setting(db, "base_domain", settings.base_domain))
    console_host = f"console.{domain}"
    listen_port = settings.caddy_listen_port or (443 if settings.public_scheme == "https" else 80)
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
    server: dict = {
        # Plain HTTP deployments (PUBLIC_SCHEME=http) listen on :80 and skip TLS
        # automation entirely; that is how the stack is exercised over an IP.
        "listen": [f":{listen_port}"],
        "routes": routes,
    }
    config: dict = {
        "admin": {"listen": "0.0.0.0:2019"},
        "apps": {"http": {"servers": {"srv0": server}}},
    }
    if settings.caddy_access_log_enabled:
        # Log writers live in the TOP-LEVEL `logging.logs` map; the server only
        # names the logger it uses. Putting a `logs` key on the server or on the
        # http app is rejected by Caddy with `unknown field "logs"` (this exact
        # shape was verified against Caddy 2.8/2.9 with `caddy adapt`).
        sink_host = "console-worker"
        if not _log_sink_reachable(sink_host, settings.caddy_log_port):
            # Routing matters more than access logging: ship the config without
            # the writer so the console works on the very first boot, and let the
            # next reconcile pick the writer up once the worker exists.
            log.warning(
                "access-log sink %s:%s is not resolvable yet; applying the config "
                "without it (the next reconcile will retry)",
                sink_host,
                settings.caddy_log_port,
            )
        else:
            logger = "access"
            server["logs"] = {"default_logger_name": logger}
            config["logging"] = {
                "logs": {
                    "default": {"exclude": [f"http.log.access.{logger}"]},
                    logger: {
                        "writer": {
                            "output": "net",
                            "address": f"{sink_host}:{settings.caddy_log_port}",
                        },
                        "encoder": {"format": "json"},
                        "level": "INFO",
                        "include": [f"http.log.access.{logger}"],
                    },
                }
            }
    if settings.public_scheme == "https":
        config["apps"]["tls"] = {
            "automation": {
                "policies": [
                    {
                        "subjects": [f"*.{domain}", console_host],
                        "issuers": [{"module": "internal"}],
                    }
                ]
            }
        }
    return config


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
