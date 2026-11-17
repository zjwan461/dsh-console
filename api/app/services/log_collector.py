from __future__ import annotations

import asyncio
import json
import logging
import time

from ..config import settings
from ..db import SessionLocal
from ..models import Instance, InstanceStatus
from . import metering

log = logging.getLogger("dsh.access-log")

_cache: dict = {"at": 0.0, "map": {}}
_CACHE_TTL = 20.0


def _instance_map() -> dict[str, int]:
    now = time.monotonic()
    if now - _cache["at"] < _CACHE_TTL and _cache["map"]:
        return _cache["map"]
    with SessionLocal() as db:
        rows = (
            db.query(Instance.subdomain, Instance.id)
            .filter(Instance.status != InstanceStatus.deleted)
            .all()
        )
    _cache["map"] = {row[0]: row[1] for row in rows}
    _cache["at"] = now
    return _cache["map"]


def handle_line(line: str) -> None:
    line = line.strip()
    if not line or not line.startswith("{"):
        return
    try:
        entry = json.loads(line)
    except json.JSONDecodeError:
        return
    request = entry.get("request") or {}
    host = str(request.get("host") or "").split(":", 1)[0].lower()
    if not host:
        return
    domain = settings.base_domain.lower()
    if not host.endswith("." + domain):
        return
    subdomain = host[: -(len(domain) + 1)]
    instance_id = _instance_map().get(subdomain)
    if instance_id is None:
        return
    metering.touch(instance_id)


async def _client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            if len(line) > 1_000_000:
                continue
            handle_line(line.decode("utf-8", errors="replace"))
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


async def serve() -> None:
    server = await asyncio.start_server(_client, settings.caddy_log_listen, settings.caddy_log_port)
    log.info(
        "access log collector listening on %s:%s",
        settings.caddy_log_listen,
        settings.caddy_log_port,
    )
    async with server:
        await server.serve_forever()
