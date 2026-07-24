#!/bin/sh
# Runs inside the compose "backup" service: periodic pg_dump with retention.
# Backups land in ./backups on the host. Copy them off the machine — a backup
# on the same disk as the database only survives half of the failure modes.
set -eu

INTERVAL_HOURS="${BACKUP_INTERVAL_HOURS:-24}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
mkdir -p /backups

echo "Backup loop started: every ${INTERVAL_HOURS}h, keeping ${RETENTION_DAYS} days."
while true; do
  stamp="$(date -u +%Y%m%d-%H%M%S)"
  target="/backups/${PGDATABASE}-${stamp}.dump"
  if pg_dump --format=custom --file="${target}.partial"; then
    mv "${target}.partial" "${target}"
    echo "Backup written: ${target} ($(du -h "${target}" | cut -f1))"
  else
    rm -f "${target}.partial"
    echo "BACKUP FAILED at ${stamp}" >&2
  fi
  find /backups -name "*.dump" -mtime "+${RETENTION_DAYS}" -delete
  sleep "$((INTERVAL_HOURS * 3600))"
done
