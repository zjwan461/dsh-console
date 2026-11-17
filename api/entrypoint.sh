#!/bin/sh
set -e

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
