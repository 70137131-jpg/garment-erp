# Garment Manufacturing ERP

A single source of truth for a garment manufacturing business — see
[`docs/blueprint.md`](docs/blueprint.md) for the full functional blueprint and
[`docs/08-Improvement-Plan.md`](docs/08-Improvement-Plan.md) for the phased improvement plan.

Stack: **FastAPI + SQLModel + Alembic** backend, **React + Vite + TypeScript**
frontend (SQLite for local dev, Postgres in production). Money and quantities are
`Decimal` with defined precision; the stock ledger and accounting journals are
append-only; specifications (BOMs, cost sheets) are versioned; document numbers
are gap-free and sequential.

## Build status

| Phase | Scope | Status |
|-------|-------|--------|
| 0 | Kernel: numbering, state machine, immutable-ledger pattern, versioning, audit, event seam, RBAC | ✅ done |
| 1 | Master data (customers, suppliers, materials w/ dual UoM, colours, seasons, size ranges, styles, colourways, versioned per-size BOM) | ✅ done |
| 2 | Immutable stock ledger + roll register | ✅ done |
| 3 | Sales orders with normalized size matrix + confirmation event | ✅ done |
| 4 | Procurement + goods receipt → rolls + four-point incoming QC | ✅ done |
| 5 | Reservations + roll-selection logic | ✅ done |
| 6 | Production (cut/sew, size-BOM fabric calc, SAM efficiency) + subcontracting | ✅ done |
| 7 | Inline DHU + AQL final inspection (gates shipment) | ✅ done |
| 8 | Costing (versioned cost sheets, SAM, order profitability) | ✅ done |
| 9 | Finance (CoA, double-entry journals, event-driven auto-posting, AR/AP, P&L) | ✅ done |
| 10 | Harden: RBAC, segregation of duties, approval workflows, audit, **Part C end-to-end acceptance** | ✅ done |

The order-to-cash spine runs end to end. The **Part C acceptance scenario**
(TS-100 masters → sales → procurement → quality → inventory → production →
quality → costing → finance → P&L) is executed as a single test:
[`backend/tests/test_acceptance_partc.py`](backend/tests/test_acceptance_partc.py).

## Business operations expansion

The production workbenches now include:

- Search, filters, client pagination, server paging parameters, sorting, and
  spreadsheet-safe CSV exports for the primary operational registers.
- Ledger-backed warehouse transfers, returns, put-away, roll split/join,
  regrading, and cycle counts with durable operation histories.
- Sales-order amendments and cancellations with immutable snapshots, pricing
  checks, and customer credit-exposure gating before confirmation.
- Optional PO approval gates, PO amendment history, persistent receipt history,
  and supplier delivery/fulfilment/quality performance.
- Versioned production routes, append-only WIP movement capture, persistent
  sewing/subcontract registers, and step-level WIP balances.
- Incoming/inline/final inspection history, defect Pareto analytics, and a lab
  test register with pass/fail completion and failed-roll quarantine.
- General-ledger inquiry, trial balance, balance sheet, cash-flow classification,
  AR/AP aging, and exports.
- Browser-ready PDFs for purchase orders, invoices, goods receipts, cut orders,
  and sewing work orders with repeating table headers and page numbers.

The expansion is covered by
[`backend/tests/test_business_expansion.py`](backend/tests/test_business_expansion.py).

## Architecture at a glance

- **Kernel** (`app/kernel`) — cross-cutting primitives: gap-free document
  numbering, declarative state machines, audit timestamps, the synchronous
  domain-event dispatcher, RBAC, idempotency keys, and Decimal precision helpers.
- **Domain modules** (`app/masters`, `styles`, `inventory`, `sales`,
  `procurement`, `quality`, `production`, `costing`, `finance`) — each with
  `models.py` (SQLModel tables + API schemas), `service.py` (business rules /
  transactions), and `router.py` (HTTP endpoints).
- **Event seam** (`app/events.py`) — operations emit events
  (`SalesOrderConfirmed`, `GoodsReceiptPosted`, `ProductionConfirmed`,
  `ShipmentDispatched`, …); Finance subscribes and auto-posts accounting into the
  *same* transaction. Operations never import Finance.

## Running locally

**Backend** (terminal 1):

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# create the schema (dev, SQLite) and run the API
uvicorn app.main:app --reload
# API docs at http://127.0.0.1:8000/docs

# run the test suite (including the Part C acceptance scenario)
pytest
```

The API seeds a default chart of accounts and wires finance auto-posting on
startup.

**Frontend** (terminal 2):

```bash
cd frontend
npm install
npm run dev
# open http://localhost:5173
```

The Vite dev server proxies `/api/*` to the backend on port 8000. Build for
production with `npm run build` (emits static assets to `frontend/dist`).

## Frontend

A React + TypeScript single-page app covering the full spine, with an
"industrial atelier" design system (IBM Plex type, warm-paper canvas, indigo +
madder dye accents). Pages: Dashboard, Sales Orders (size-matrix entry),
Styles & BOM, Costing, Procurement + Goods Receipt, Inventory (rolls / ledger /
reservations), Production (cut / sew / subcontract), Quality (four-point / DHU /
AQL), Master Data, and Finance (journals / AR / AP / P&L). Users sign in with
centrally managed accounts. The SPA uses an HttpOnly, SameSite session cookie;
roles cannot be selected or asserted by the browser. Administrators manage
accounts and role assignments from **Access Control**.

## Deployment & operations

The whole stack ships as Docker Compose (Postgres + API + SPA + scheduled
backups). Version is tracked in [`VERSION`](VERSION) and changes in
[`CHANGELOG.md`](CHANGELOG.md); `GET /health` reports the running version and
database status, and `GET /ready` gates traffic on the schema being at the
expected migration head.

```bash
cp .env.example .env       # set POSTGRES_PASSWORD + bootstrap admin credentials
docker compose up -d       # SPA on http://localhost:8080, migrations run on boot
```

Internet-facing deployment (automatic HTTPS via Caddy — set `DOMAIN` in `.env`):

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Operations:

- **Backups** — the `backup` service runs `pg_dump` on a schedule into
  `./backups/` (`BACKUP_INTERVAL_HOURS`, `BACKUP_RETENTION_DAYS`); manual
  `./scripts/backup.sh` and `./scripts/restore.sh <dump>` are provided. Copy
  backups off the machine. An untested backup is not a backup.
- **Upgrades** — `./scripts/upgrade.sh` takes a pre-upgrade backup, rebuilds
  images, rolls the stack, and waits for `/ready`.
- **Logging** — structured JSON logs (`LOG_FORMAT=json`) with a request ID on
  every line; the same ID is returned to clients as `X-Request-ID` and inside
  error responses. Optional Sentry via `SENTRY_DSN`.
- **Demo data** — `python -m app.seed_demo` seeds a realistic factory
  (refuses production environments and non-empty databases).
- **Onboarding imports** — admin-only CSV imports for customers, suppliers,
  materials, and opening stock at `/imports/*` with dry-run validation
  reports and downloadable templates (`/imports/templates/{kind}`).
- **Attachments** — files attach to materials, customers, suppliers, styles,
  and orders; stored under `ATTACHMENTS_DIR` (a named volume in Compose).

## Database migrations (production)

Local dev auto-creates tables on startup. Production uses Alembic against
Postgres:

```bash
export DATABASE_URL="postgresql+psycopg://user:pass@host:5432/garment_erp"
alembic upgrade head          # apply migrations
alembic revision --autogenerate -m "describe change"   # author a new migration
```

## Access control

Every business endpoint requires an authenticated, active user. Passwords are
Argon2id-hashed and browser sessions are random, expiring credentials whose
hashes are stored server-side for immediate revocation. Database role assignments
drive module read permissions and action-level segregation of duties; `X-Role`
headers are ignored. Login attempts and account changes are security-audited.
Browser mutations additionally require a double-submit CSRF token, temporary
passwords must be replaced before ERP access, sessions expire after inactivity,
and login attempts are throttled by both account and client IP. Responses carry
defensive browser headers and reject oversized request bodies.

For the first production startup, set `BOOTSTRAP_ADMIN_EMAIL` and a random
`BOOTSTRAP_ADMIN_PASSWORD` of at least 15 characters. Remove both settings once
the administrator exists. Also set `SESSION_COOKIE_SECURE=true`, `ALLOWED_HOSTS`,
and the HTTPS `CORS_ORIGINS` value; see `backend/.env.example`.
Set `AUTO_CREATE_SCHEMA=false` in production and apply `alembic upgrade head`
before starting the application.

If every administrator is locked out, recover an existing account from the
backend directory. The command prompts securely and never accepts the password
as a command-line argument:

```bash
python -m app.security.cli reset-password --email admin@example.com
```

The reset revokes existing sessions and requires the user to choose a new
password at the next sign-in. Administrators can also inspect and revoke
sessions, reset user passwords, and review security audit events from **Access
Control**.
Set `ENVIRONMENT=production` as well: startup will then reject SQLite, insecure
cookies, development hosts/origins, automatic schema creation, and public API
documentation instead of silently launching with an unsafe configuration.
