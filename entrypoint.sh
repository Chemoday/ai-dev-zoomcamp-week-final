#!/bin/sh
# Apply database migrations (rollback-safe, see src/core/migrations.py), then start the API.
set -e

python -m src.core.migrations

exec uvicorn src.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --proxy-headers \
    --forwarded-allow-ips "*" \
    --no-access-log
