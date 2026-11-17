#!/bin/sh
set -e

# `docker compose run console-api <cmd>` and the worker service pass a command.
# In that case do not run migrations (the API service owns them).
if [ "$#" -gt 0 ]; then
  exec "$@"
fi

# Apply schema migrations before serving. Safe to run on every boot: Alembic is
# idempotent and exits non-zero if a migration fails, which keeps a broken build
# from serving traffic.
alembic upgrade head

exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --workers "${API_WORKERS:-2}" \
  --proxy-headers \
  --forwarded-allow-ips "*"
