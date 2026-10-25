#!/bin/sh
# Apply database migrations, then start the API server.
set -e

alembic upgrade head

exec uvicorn src.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --proxy-headers \
    --no-access-log
