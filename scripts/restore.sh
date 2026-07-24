#!/bin/sh
# Restore a pg_dump custom-format backup into the compose Postgres database.
# DESTRUCTIVE: drops and recreates the application schema contents.
# Usage: ./scripts/restore.sh backups/garment_erp-20260723-020000.dump
set -eu
cd "$(dirname "$0")/.."

BACKUP_FILE="${1:?usage: ./scripts/restore.sh <backup.dump>}"
[ -f "$BACKUP_FILE" ] || { echo "No such file: $BACKUP_FILE" >&2; exit 1; }

printf 'This will OVERWRITE the current database with %s. Type yes to continue: ' "$BACKUP_FILE"
read -r answer
[ "$answer" = "yes" ] || { echo "Aborted."; exit 1; }

echo "Stopping application so no writes race the restore..."
docker compose stop backend

docker compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner' < "$BACKUP_FILE"

echo "Restarting application (migrations re-run on boot)..."
docker compose start backend
echo "Restore complete. Verify with: curl -s localhost:8080/api/health"
