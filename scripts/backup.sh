#!/bin/sh
# One-off manual backup of the compose Postgres database.
# Usage: ./scripts/backup.sh [output-dir]   (default: ./backups)
set -eu
cd "$(dirname "$0")/.."

OUT_DIR="${1:-./backups}"
mkdir -p "$OUT_DIR"
stamp="$(date -u +%Y%m%d-%H%M%S)"
target="${OUT_DIR}/manual-${stamp}.dump"

docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' > "$target"
echo "Backup written: $target"
