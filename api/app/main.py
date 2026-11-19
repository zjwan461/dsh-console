from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from .config import settings
from .db import SessionLocal
from .errors import ApiError, api_error_handler, error_body, unhandled_error_handler
from .models import User  # noqa: F401  (registers metadata)
from .redis import redis_ok
from .routers import (
    admin_audit,
    admin_images,
    admin_instances,
    admin_invites,
    admin_metrics,
    admin_plans,
    admin_plugins,
    admin_quotas,
    admin_settings,
    admin_skills,
    admin_users,
    announcements,
    auth,
    events,
    files,
    instances,
    me,
    me_skills,
    snapshots,
    system,
    tickets,
    user_images,
)
from .security import new_request_id
from .seed import seed_all
from .version import __version__

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("dsh.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    import os

    os.makedirs(settings.data_root, exist_ok=True)
    # Always converge the schema before touching any table: this is what makes
    # in-place upgrades "just work" in dev and prod alike.
    from .migrations import ensure_schema

    ensure_schema()
    with SessionLocal() as db:
        try:
            seed_all(db)
        except SQLAlchemyError:
            log.exception("seed failed (database not ready?)")
            raise
        # Databases created before the defaults were env-backed still hold the
        # old hardcoded base domain, which would keep `CONSOLE_BASE_DOMAIN` inert
        # and make Caddy issue certificates for the wrong name.
        try:
            from .services.settings import adopt_env_defaults

            adopted = adopt_env_defaults(db)
        except Exception:
            log.exception("could not adopt environment defaults")
            adopted = []
        if adopted:
            log.warning("settings adopted from the environment: %s", ", ".join(adopted))
    try:
        from .drivers import get_driver

        get_driver().ensure_network(settings.instance_network)
    except Exception:
        log.warning("container runtime is not reachable at startup", exc_info=True)
    try:
        from .services import caddy

        with SessionLocal() as db:
            for attempt in range(10):
                try:
                    caddy.apply(db)
                    break
                except Exception:
                    if attempt == 9:
                        raise
                    import time

                    time.sleep(1)
    except Exception as exc:
        # Expected on a cold start: `console-worker` (the access-log sink) may not
        # exist yet. build_config already drops the writer in that case, so this
        # is only for anything else; the worker's first reconcile re-applies.
        log.warning(
            "could not publish Caddy config at startup (%s); the next reconcile retries", exc
        )
    log.info("api ready env=%s prefix=%s", settings.env, settings.api_prefix)
    yield


app = FastAPI(
    title="DSH Console API",
    version=__version__,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.middleware("http")
async def observability(request: Request, call_next):
    request.state.request_id = new_request_id()
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Request-Id"] = request.state.request_id
    response.headers["X-Response-Time-Ms"] = f"{elapsed_ms:.1f}"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    return response


app.add_exception_handler(ApiError, api_error_handler)
app.add_exception_handler(Exception, unhandled_error_handler)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    fields = []
    for item in exc.errors():
        loc = ".".join(str(part) for part in item.get("loc", []) if part != "body")
        fields.append({"field": loc, "message": item.get("msg", "invalid")})
    return JSONResponse(
        status_code=422,
        content=error_body(request, "VALIDATION_ERROR", "参数校验失败", {"fields": fields}),
    )


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"status": "ok"}


@app.get("/readyz", include_in_schema=False)
def readyz():
    checks = {"redis": redis_ok()}
    try:
        with SessionLocal() as db:
            db.execute(__import__("sqlalchemy").text("SELECT 1"))
        checks["mysql"] = True
    except Exception:
        checks["mysql"] = False
    return JSONResponse(
        status_code=200 if all(checks.values()) else 503,
        content={"status": "ok" if all(checks.values()) else "degraded", "checks": checks},
    )


for router in (
    auth.router,
    me.router,
    instances.router,
    snapshots.router,
    user_images.router,
    files.router,
    tickets.router,
    announcements.router,
    events.router,
    admin_users.router,
    admin_plans.router,
    admin_invites.router,
    admin_quotas.router,
    admin_images.router,
    admin_instances.router,
    admin_metrics.router,
    admin_settings.router,
    admin_skills.router,
    admin_plugins.router,
    admin_audit.router,
    system.router,
    me_skills.router,
):
    app.include_router(router, prefix=settings.api_prefix)

# Browser-facing entry points are NOT part of the versioned API: Caddy rewrites
# per-user hosts to `/w/{wake_key}`, and the launch URL is `/go/{ticket}`. Mounting
# them under the API prefix would 404 (or fall through to the SPA and render the
# login page instead of redirecting).
app.include_router(instances.public_router)
# WebSocket routes get their own no-CSRF router (see admin_instances).
app.include_router(admin_instances.ws_router, prefix=settings.api_prefix)
