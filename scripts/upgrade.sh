#!/bin/sh
# Upgrade a running compose deployment to the currently checked-out version.
# Takes a database backup first, rebuilds images, and rolls the stack.
# Usage: ./scripts/upgrade.sh [compose-args...]
#   e.g. ./scripts/upgrade.sh                          (local/evaluation stack)
#        ./scripts/upgrade.sh -f docker-compose.yml -f docker-compose.prod.yml
set -eu
cd "$(dirname "$0")/.."

COMPOSE="docker compose $*"

echo "==> Version to deploy: $(cat VERSION)"

echo "==> Pre-upgrade database backup..."
mkdir -p backups
stamp="$(date -u +%Y%m%d-%H%M%S)"
$COMPOSE exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' \
  > "backups/pre-upgrade-${stamp}.dump"
echo "    backups/pre-upgrade-${stamp}.dump"

echo "==> Building images..."
$COMPOSE build

echo "==> Rolling the stack (migrations run on backend boot)..."
$COMPOSE up -d

echo "==> Waiting for the API to become ready..."
attempt=0
until $COMPOSE exec -T backend python -c \
  "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/ready', timeout=3).status==200 else 1)" \
  2>/dev/null; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 30 ]; then
    echo "API did not become ready. Check: $COMPOSE logs backend" >&2
    echo "Roll back with: ./scripts/restore.sh backups/pre-upgrade-${stamp}.dump" >&2
    exit 1
  fi
  sleep 2
done

echo "==> Upgrade complete: $($COMPOSE exec -T backend python -c \
  "import urllib.request,json; print(json.load(urllib.request.urlopen('http://localhost:8000/health'))['version'])")"
