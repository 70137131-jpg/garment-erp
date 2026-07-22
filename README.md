# Garment Manufacturing ERP

A single source of truth for a garment manufacturing business — see
[`docs/blueprint.md`](docs/blueprint.md) for the full functional blueprint and
[`docs/build-plan.md`](docs/build-plan.md) for the phased build plan.

Stack: **FastAPI + SQLModel + Alembic** (SQLite for local dev, Postgres in
production). Money and quantities are `Decimal` with defined precision; the stock
ledger and accounting journals are append-only; specifications (BOMs, cost
sheets) are versioned; document numbers are gap-free and sequential.

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

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# create the schema (dev, SQLite) and run the API
uvicorn app.main:app --reload
# open http://127.0.0.1:8000/docs

# run the test suite (66 tests, including the Part C acceptance scenario)
pytest
```

The API seeds a default chart of accounts and wires finance auto-posting on
startup.

## Database migrations (production)

Local dev auto-creates tables on startup. Production uses Alembic against
Postgres:

```bash
export DATABASE_URL="postgresql+psycopg://user:pass@host:5432/garment_erp"
alembic upgrade head          # apply migrations
alembic revision --autogenerate -m "describe change"   # author a new migration
```

## Access control (dev)

RBAC is enforced via an `X-Role` header (e.g. `X-Role: merchandiser`). `admin`
bypasses all gates. Real authentication (users, sessions) replaces the header in
a later iteration; the permission surface is already attached to every endpoint.
