# Changelog

All notable changes to the Garment ERP are recorded here. The version number
lives in [`VERSION`](VERSION) and is reported by `GET /health`.

## 0.2.0 — 2026-07-23

Production-readiness release: the system can now be deployed, operated, and
onboarded for a customer.

### Added
- **Structured logging** — JSON logs with request ID, user, status, and
  duration on every request; global exception handler returns a clean 500
  carrying the request ID; optional Sentry error tracking (`SENTRY_DSN`).
- **Health/readiness** — `/health` pings the database; `/ready` verifies the
  schema is at the expected migration head before a pod receives traffic.
- **Docker deployment** — backend and frontend Dockerfiles, a Compose stack
  (Postgres + API + SPA + scheduled backups), and a production override with
  automatic HTTPS via Caddy. Migrations run on boot.
- **CI** — GitHub Actions running the backend suite on SQLite **and**
  PostgreSQL, a full migration up/down/up cycle, frontend typecheck/build,
  and a Docker image boot smoke test.
- **Backups** — scheduled `pg_dump` with retention in Compose, plus manual
  `scripts/backup.sh` / `scripts/restore.sh`.
- **CSV imports** (`/imports/*`, admin-only) — customers, suppliers,
  materials, and opening stock with row-level validation reports,
  all-or-nothing apply, and downloadable header templates.
- **Attachments** (`/attachments`) — files linked to materials, customers,
  suppliers, styles, sales orders, and purchase orders; extension allowlist,
  10 MB limit, uploader/admin delete.
- **Demo seed** — `python -m app.seed_demo` builds a realistic factory
  (role users, masters, styles, three orders at different lifecycle stages)
  through the real API.

### Fixed
- **Timezone-skew bug**: PostgreSQL connections are now pinned to UTC.
  Previously a database server in a non-UTC zone silently skewed session
  expiry, lockout, and audit timestamps.
- **Postgres error mapping**: immutability triggers now raise SQLSTATE 23000
  so drivers report `IntegrityError`, matching SQLite behavior.
- **Migration reversibility**: downgrading the initial schema now drops the
  native Postgres ENUM types, so a downgrade→upgrade cycle works.
- Test fixtures now create real user rows for principals (Postgres enforces
  the audit-event foreign keys that SQLite silently ignored).

## 0.1.0 — 2026-07-22

Initial order-to-cash spine: kernel (numbering, immutable ledger, RBAC,
audit), masters, styles/BOM, inventory with roll register, sales,
procurement, quality, production, costing, finance, server-side
authentication and sessions, documents/PDF, and the React SPA.
