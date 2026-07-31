# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

The virtualenv lives at the **repo root** (`.venv`), not in `backend/`, despite what the README says.
All backend commands run from `backend/`.

```powershell
# activate (Windows)
.\.venv\Scripts\Activate.ps1

cd backend
uvicorn app.main:app --reload        # API on :8000, docs at /docs
pytest                               # whole suite (in-memory SQLite)
pytest tests/test_finance.py -q      # one file
pytest tests/test_finance.py::test_name -q
pytest -m concurrency                # tests needing real Postgres locking
pytest -k "shipment" -q

python -m app.seed_demo              # realistic factory data (refuses prod / non-empty DB)
python -m app.security.cli reset-password --email admin@example.com

# migrations (production path; dev auto-creates the schema)
alembic upgrade head
alembic revision --autogenerate -m "describe change"
```

```powershell
cd frontend
npm run dev      # :5173, proxies /api/* -> 127.0.0.1:8000 (strips the /api prefix)
npm run build    # tsc -b && vite build — this IS the typecheck; there is no separate lint step
```

Run the suite against PostgreSQL the way CI does (matrix: sqlite + postgres):

```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://erp:erp@localhost:5432/erp_test"; pytest tests/ -q
```

There is no linter or formatter configured. CI (`.github/workflows`) runs pytest on both dialects,
an alembic upgrade→downgrade→upgrade cycle on Postgres, and `npm run build`.

Docker: `docker compose up -d` (SPA on :8080, migrations on boot); add `-f docker-compose.prod.yml`
for the Caddy/HTTPS overlay. `scripts/upgrade.sh`, `backup.sh`, `restore.sh` handle ops.

## Architecture

FastAPI + SQLModel + Alembic backend, React + Vite SPA. SQLite for dev, Postgres in production.
`docs/blueprint.md` is the functional spec; `docs/08-Improvement-Plan.md` is the current remediation
backlog and is worth reading before structural changes.

### Module shape

`app/<domain>/` — `models.py` (SQLModel tables **and** the Pydantic request/response schemas),
`service.py` (business rules, transactions), `router.py` (HTTP). Domains: `masters`, `styles`,
`inventory`, `sales`, `procurement`, `quality`, `production`, `costing`, `planning`, `mes`, `wms`,
`finance`, plus `tna`, `workforce`, `imports`, `attachments`, `documents` (PDF), `security`.

Two domains split their service layer by concern rather than using a single `service.py`:
`costing/actuals.py` (variance engine) and `planning/mrp.py` + `planning/capacity.py`.

`mes` (shift execution, OEE) and `wms` (bins, directed tasks, scanning) are *execution* layers over
existing domains, so they reuse `production_read` / `inventory_read` rather than adding permissions.
`wms` never writes stock itself — completing a task calls `inventory.service.move_stock`.

`app/models.py` is an aggregate import of every `models.py`. **A new table must be added there** or
`init_db()` and alembic autogenerate will not see it.

### The event seam (the load-bearing piece)

Operations emit events from `app/events.py` (`SalesOrderConfirmed`, `GoodsReceiptPosted`,
`ProductionConfirmed`, `ShipmentDispatched`, `SupplierInvoiceRaised`) via the synchronous dispatcher
in `app/kernel/events.py`. Finance subscribes in `app/finance/posting.py` and posts journals **into
the same transaction** — operations never import finance.

The dispatcher passes only the event, so subscribers get the active `Session` from the contextvar in
`app/kernel/context.py`. `app.db.get_session` is `async` *deliberately*: a sync dependency would set
the contextvar in a threadpool context the endpoint never sees. Don't change that signature.

Finance handlers no-op when the chart of accounts is unseeded — an operation must never fail because
finance is not configured.

### Kernel invariants (`app/kernel/`)

- `numbering.py` — gap-free `PREFIX-YYYY-00001` numbers via a `SELECT … FOR UPDATE` on
  `document_sequence`. All document numbers go through `next_document_number`.
- `immutability.py` — DB triggers (SQLite *and* Postgres) make `stock_ledger_entry`, `journal_line`,
  and `security_audit_event` append-only; `journal_entry` may only transition posted→reversed.
  Corrections are new entries, never updates. Dev installs guards in `init_db()`; production gets
  them from a migration; the Postgres test fixture installs them explicitly to stay faithful.
- `types.py` — money `Numeric(18,2)`, quantity `Numeric(18,4)`, rate `Numeric(18,6)`. Use
  `money_field()` / `quantity_field()` / `rate_field()` on every new numeric column. Never floats.
- `idempotency.py` — shop-floor capture endpoints (goods receipt, daily output) claim a
  `(scope, client_key)` key under a unique constraint before the business transaction, then attach
  the created `resource_id`; replays short-circuit.
- `state_machine.py` — declarative transition guards; call `assert_transition` before any status change.
- `query.py` — `page_bounds` (max 500) and `csv_download` (formula-injection-safe exports).

`inventory/service.py::post_movement` is the only way stock changes — that is what makes
"balance = sum of ledger lines" a guarantee. It never commits; callers own the transaction.

### Two invariants that are easy to break

- **Variance decomposition must reconcile.** `costing/actuals.py` states every variance as a
  residual, not the textbook product form, so `actual_total − std_total == Σ variances` holds even
  in the awkward cases (nothing issued, zero minutes recorded, material consumed with no standard).
  `tests/test_actual_costing.py::_assert_reconciles` guards it. Posting a run is a *reclassification*
  out of COGS — the GL is already actual-cost driven, so a second charge would double-count.
- **MRP nets sequentially; per-order shortage does not.** `sales.service.recalculate_material_
  requirements` nets each order against total stock independently, so two orders can both claim the
  same stock — correct for "can this one order be covered now", wrong for planning. `planning/mrp.py`
  carries a running balance across buckets. The contrast is asserted directly in
  `test_planning.py::test_mrp_nets_sequentially_unlike_per_order_shortage`; don't "fix" either one
  to match the other.

### Authorization (two layers)

1. Router-level *read* permission in `main.py`: each `include_router` carries
   `Depends(require_permissions(Permission.<domain>_read))`.
2. Endpoint-level *segregation of duties*: mutating endpoints add
   `Depends(require_roles(Role.merchandiser, …))`, which returns the principal's email for audit
   fields. Admin bypasses role checks; `X-Role` headers are ignored entirely.

Roles/permissions are defined in `app/kernel/rbac.py` (`ROLE_PERMISSIONS`). `workforce_read` is
deliberately excluded from the all-read bundle. Sessions are HttpOnly cookies backed by revocable DB
rows; browser mutations need the double-submit `X-CSRF-Token`.

`config.py` hard-fails at startup in `ENVIRONMENT=production` on SQLite, auto-create schema, open API
docs, insecure cookies, non-HTTPS CORS origins, or localhost in `ALLOWED_HOSTS`.

### Frontend

`src/api/client.ts` is the single fetch wrapper: prefixes `/api`, fetches a CSRF token before unsafe
methods, and dispatches an `erp:unauthorized` window event on 401 so `App.tsx` drops to the login
screen. Routes in `App.tsx` are lazy-loaded and gated by `user.permissions` strings that mirror the
backend `Permission` enum — add a permission in both places. `src/api/types.ts` mirrors backend
schemas by hand; there is no codegen.

## Tests

`tests/conftest.py` provides `session` (in-memory SQLite per test by default; savepoint-isolated
Postgres session when `TEST_DATABASE_URL` is set), `client` (authenticated as a real admin row, CSRF
primed), and `finance_client` (adds subscribers + seeded chart of accounts). Domain tests assume the
admin principal; RBAC behavior is tested explicitly in `test_rbac_audit.py`.

`tests/test_acceptance_partc.py` is the end-to-end order-to-cash scenario (masters → sales →
procurement → quality → inventory → production → quality → costing → finance → P&L). Treat a break
there as a regression in the spine, and keep it passing when touching cross-module flows.

## Conventions

- Schema changes need an Alembic migration even though dev auto-creates tables; CI verifies the
  migration downgrades cleanly.
- Version lives in `VERSION` (read by `config.py`, reported by `/health`); notable changes go in
  `CHANGELOG.md`.
- `/health` = liveness + DB; `/ready` = schema at migration head (503 otherwise).
