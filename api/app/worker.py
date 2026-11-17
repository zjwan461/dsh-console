from __future__ import annotations

import asyncio
import logging
import signal

from .config import settings
from .db import SessionLocal
from .services import log_collector, node_metrics, worker_tasks

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("dsh.worker")


def _run(fn, *args):
    with SessionLocal() as db:
        return fn(db, *args)


async def every(seconds: int, name: str, fn, *args) -> None:
    while True:
        try:
            result = await asyncio.to_thread(_run, fn, *args)
            if result:
                log.debug("%s: %s", name, result)
        except Exception:
            log.exception("%s failed", name)
        await asyncio.sleep(seconds)


async def daily(name: str, fn) -> None:
    while True:
        try:
            await asyncio.to_thread(_run, fn)
        except Exception:
            log.exception("%s failed", name)
        await asyncio.sleep(3600)


async def main() -> None:
    log.info(
        "worker starting: stats=%ss flush=%ss idle=%ss",
        settings.stats_interval_seconds,
        settings.usage_flush_seconds,
        settings.idle_check_seconds,
    )
    import sqlalchemy as sa

    from .db import engine

    # Wait for the API entrypoint/MySQL to be ready.
    for _ in range(30):
        try:
            with engine.connect() as conn:
                conn.execute(sa.text("SELECT 1"))
            break
        except Exception:
            await asyncio.sleep(2)

    tasks = [
        asyncio.create_task(
            every(settings.stats_interval_seconds, "stats", worker_tasks.sample_once)
        ),
        asyncio.create_task(every(settings.usage_flush_seconds, "flush", worker_tasks.flush_once)),
        asyncio.create_task(every(settings.idle_check_seconds, "idle", worker_tasks.idle_once)),
        asyncio.create_task(every(settings.usage_flush_seconds, "quota", worker_tasks.quota_once)),
        asyncio.create_task(every(3600, "reset", worker_tasks.reset_cycles)),
        asyncio.create_task(every(60, "reconcile", worker_tasks.reconcile_once)),
    ]
    if settings.node_metrics_enabled:
        tasks.append(
            asyncio.create_task(
                every(settings.node_metrics_interval, "node", worker_tasks.node_once)
            )
        )
        tasks.append(
            asyncio.create_task(every(3600, "node-prune", lambda db: node_metrics.prune(db)))
        )
    if settings.caddy_access_log_enabled:
        tasks.append(asyncio.create_task(log_collector.serve()))
    if settings.registry_check_enabled:
        tasks.append(
            asyncio.create_task(
                every(
                    settings.registry_check_interval_hours * 3600,
                    "registry",
                    worker_tasks.registry_once,
                )
            )
        )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            pass
    await stop.wait()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
