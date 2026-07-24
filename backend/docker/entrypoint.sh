#!/bin/sh
# Container entrypoint: wait for the database, apply migrations, start the app.
set -e

if [ "${RUN_MIGRATIONS:-true}" != "false" ]; then
  echo "Waiting for database and applying migrations..."
  attempt=0
  until alembic upgrade head; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 30 ]; then
      echo "Database was not ready after 30 attempts; giving up." >&2
      exit 1
    fi
    echo "Database not ready (attempt ${attempt}/30); retrying in 2s..."
    sleep 2
  done
  echo "Migrations are up to date."
fi

exec gunicorn app.main:app \
  --worker-class uvicorn.workers.UvicornWorker \
  --workers "${WEB_CONCURRENCY:-4}" \
  --bind 0.0.0.0:8000 \
  --timeout "${REQUEST_TIMEOUT_SECONDS:-60}" \
  --graceful-timeout 30 \
  --error-logfile - \
  --log-level warning
