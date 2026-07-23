# Technical Architecture Document — Textile & Apparel ERP

**Document:** 02 — Technical Architecture
**Status:** Draft v0.1
**Owner:** Rupesh
**Last updated:** 23 July 2026
**Related:** `01-PRD-textile-erp.md`, `03-Data-Model.md`

---

## 1. Purpose

This document describes how the ERP is built: the stack, the system structure, module boundaries, cross-cutting concerns, and deployment shape. It assumes the scope defined in the PRD.

## 2. Architectural Principles

These principles govern every design decision that follows. Where a trade-off arises, resolve it in favour of the principle listed higher.

1. **Transactions are immutable.** Stock movements and financial postings are never edited or deleted. Corrections are made by posting a reversing entry. This is non-negotiable — it is what makes the system auditable.
2. **API-first.** Every capability is a REST endpoint. The UI is one client among several; a future supplier portal or barcode scanner is another. No business logic lives in the UI layer.
3. **The database enforces integrity.** Foreign keys, unique constraints, and check constraints are declared at the database level, not only in application code. Application validation is a convenience, not the guarantee.
4. **Business logic lives in a service layer,** not in views, not in serialisers, not in model save methods. Views handle HTTP; services handle meaning.
5. **Modules own their data.** A module reads other modules' data through their service interfaces or defined read models, not by reaching into their tables with ad-hoc joins.
6. **Boring technology.** Prefer well-understood, well-documented tools over novel ones. This system will be maintained for years by people who did not build it.

## 3. Technology Stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Language | Python 3.12+ | Team familiarity; mature ecosystem for business applications |
| Web framework | Django 5.x | Batteries-included ORM, admin, auth, and migrations suit ERP CRUD density |
| API layer | Django REST Framework | Mature serialisation, permissions, and viewset conventions |
| Database | PostgreSQL 16+ | Transactional integrity, row-level locking, JSONB for flexible attributes, mature partitioning |
| Async tasks | Celery + Redis | MRP explosion, report generation, and document rendering run off the request cycle |
| Cache | Redis | Session store, rate limiting, and computed-report caching |
| Auth | Django auth + JWT (SimpleJWT) | Session auth for the UI, token auth for integrations |
| API docs | drf-spectacular (OpenAPI 3) | Generated from code, so it cannot drift from reality |
| Testing | pytest + pytest-django + factory_boy | Fast, expressive, good fixture ergonomics |
| Migrations | Django migrations | Version-controlled schema, reviewable in PRs |
| Containerisation | Docker + Docker Compose | Reproducible dev environment; deployment parity |

### 3.1 Deliberately deferred

- **Microservices.** A modular monolith is correct at this scale. Service extraction is a V1+ decision, made only if a specific module demonstrates independent scaling needs.
- **GraphQL.** REST is sufficient and better understood by the likely integration partners.
- **Event sourcing.** Append-only stock and GL tables give the audit benefit without the operational complexity.

## 4. System Context

```
┌─────────────┐   ┌─────────────┐   ┌──────────────┐
│  Web UI     │   │  Barcode    │   │  Integration │
│  (browser)  │   │  scanner    │   │  clients     │
└──────┬──────┘   └──────┬──────┘   └──────┬───────┘
       │                 │                  │
       └────────── HTTPS / REST ────────────┘
                         │
              ┌──────────▼──────────┐
              │   Django + DRF      │
              │   ┌──────────────┐  │
              │   │ API layer    │  │
              │   ├──────────────┤  │
              │   │ Service layer│  │
              │   ├──────────────┤  │
              │   │ Domain models│  │
              │   └──────────────┘  │
              └────┬────────┬───────┘
                   │        │
        ┌──────────▼──┐  ┌──▼──────────┐
        │ PostgreSQL  │  │ Redis       │
        └─────────────┘  └──────┬──────┘
                                │
                         ┌──────▼──────┐
                         │ Celery      │
                         │ workers     │
                         └─────────────┘
```

External systems at MVP: none mandatory. The architecture leaves room for an accounting package integration and an email/SMS gateway.

## 5. Application Structure

A **modular monolith**: one deployable Django project, internally partitioned into apps with enforced boundaries.

```
textile_erp/
├── config/                 # settings, urls, celery, wsgi/asgi
│   ├── settings/
│   │   ├── base.py
│   │   ├── dev.py
│   │   └── prod.py
│   └── celery.py
├── core/                   # cross-cutting shared kernel
│   ├── models.py           # abstract base models (timestamped, audited)
│   ├── permissions.py      # role framework
│   ├── exceptions.py       # domain exception hierarchy
│   ├── numbering.py        # document number series
│   ├── uom.py              # unit conversion service
│   └── currency.py         # multi-currency + rate capture
├── masters/                # shared master data
│   ├── customers/
│   ├── suppliers/
│   ├── materials/
│   ├── styles/
│   └── locations/
├── sales/                  # sales orders, shipments
├── procurement/            # requisitions, POs, GRN
├── inventory/              # rolls, lots, movements, reservations, valuation
├── production/             # work orders, cut plans, stage progress, subcontract
├── quality/                # inbound, in-line, final inspection, defects
├── costing/                # cost sheets, actual cost capture, variance
├── finance/                # invoices, payments, GL postings, ageing
└── reporting/              # read-model queries, exports
```

### 5.1 Layering within a module

Every module follows the same internal shape:

```
inventory/
├── models.py          # ORM models — structure and constraints only
├── services/          # business operations; the only place state changes
│   ├── receipt.py
│   ├── issue.py
│   ├── reservation.py
│   └── valuation.py
├── selectors.py       # read queries; no writes
├── serializers.py     # API request/response shapes
├── views.py           # HTTP handling only
├── urls.py
├── tasks.py           # Celery tasks
└── tests/
```

**Rule:** a view never touches an ORM model directly for writes. It calls a service. A service returns domain objects or raises domain exceptions; it does not know about HTTP.

### 5.2 Module dependency direction

Dependencies flow downward. A module may depend on anything below it, never above.

```
        reporting
            │
   ┌────────┼────────┐
finance  costing  quality
   │        │        │
   └────┬───┴────┬───┘
   production  sales
        │        │
        └───┬────┘
      procurement
            │
        inventory
            │
         masters
            │
          core
```

Where an upper module needs to notify a lower one, use a domain event rather than an upward import.

## 6. Key Design Decisions

### 6.1 Stock is a ledger, not a number

There is no `quantity_on_hand` column that gets incremented. Stock is derived from an append-only `StockMovement` table. Current stock is a sum over movements, materialised into a `StockBalance` table updated inside the same transaction as the movement.

**Why:** a mutable quantity column loses history and drifts under concurrency. A ledger cannot lie about how it reached its present state.

**Consequence:** every stock-changing operation must go through the movement service. There is no shortcut, including for corrections.

### 6.2 Roll-level identity

A fabric roll is a first-class entity with its own identity, not a quantity of a material. It carries lot, width, GSM, received length, remaining length, defect points, and its GRN lineage. Issuing fabric means consuming length from specific rolls, recorded against those roll IDs.

**Why:** apparel traceability requires answering "which roll and which shade lot went into this garment". A shade-lot mismatch discovered after sewing is an expensive failure; the data model must make it preventable.

**Consequence:** cut plans allocate specific rolls, not abstract metres. Remnants persist with their history.

### 6.3 Reservation is separate from allocation

Confirming a sales order **reserves** material — a soft claim that reduces available stock without moving it. Cut planning **allocates** specific rolls. Issue to production **moves** stock.

**Why:** three distinct business moments with different reversibility. Collapsing them causes either phantom shortages or overselling.

**Concurrency:** reservation and issue operations take a row-level lock (`SELECT FOR UPDATE`) on the affected stock balance rows for the duration of the transaction.

### 6.4 Financial postings are double-entry and append-only

Every financial event produces balanced journal entries. Entries are never modified; a reversal posts an opposite entry with a link to the original.

**Why:** partial correctness in financial data is worse than no financial data. Double-entry is self-checking.

### 6.5 Document numbering is centralised

Order numbers, PO numbers, GRN numbers, and invoice numbers are issued by a single numbering service with per-series configuration (prefix, padding, reset period) and gapless allocation under concurrency.

**Why:** sequence gaps and duplicates in commercial documents cause disputes with buyers, suppliers, and auditors.

### 6.6 Units of measure are converted, never assumed

Every material declares a base UOM. Transactions may be entered in any UOM with a declared conversion factor. Storage and valuation always use base UOM; the entry UOM and factor are retained on the record.

**Why:** fabric bought in yards, stored in metres, and consumed in metres per garment is the normal case, not an edge case.

### 6.7 Multi-currency captures the rate at the event

Transaction currency and amount are stored alongside the base-currency amount and the exchange rate used, timestamped at the transaction date. Historical documents never re-value.

## 7. Cross-Cutting Concerns

### 7.1 Authentication and authorisation

- Session authentication for the browser UI; JWT for programmatic clients.
- **Role-based access control.** Permissions are named actions (`inventory.issue_stock`, `finance.post_invoice`), grouped into roles. Roles map to users; a user may hold several.
- Enforcement happens in DRF permission classes at the API layer and is re-checked in services for state-changing operations. The UI hides what a user cannot do, but the UI is not the control.
- Object-level scoping (e.g. a planner restricted to a location) is applied via queryset filtering in selectors.

### 7.2 Audit trail

- All models inherit an abstract base providing `created_by`, `created_at`, `modified_by`, `modified_at`.
- Transactional tables (stock movements, journal entries, status transitions) are append-only.
- Master data changes are recorded in a generic `AuditLog` capturing entity, field, old value, new value, actor, and timestamp.
- The acting user is carried from request context into the service layer explicitly — never read from thread-local globals.

### 7.3 Concurrency and locking

- Optimistic locking (version column) on master records and documents, surfacing a clear conflict error rather than silently overwriting.
- Pessimistic row locks (`SELECT FOR UPDATE`) on stock balances and number series during allocation.
- All multi-step state changes run inside a single database transaction. Partial commits are not acceptable in stock or finance.

### 7.4 Error handling

A domain exception hierarchy in `core/exceptions.py` — `InsufficientStock`, `InvalidStateTransition`, `ReservationConflict`, and so on. A DRF exception handler maps these to structured HTTP responses with a machine-readable error code, a human-readable message, and field-level detail where applicable. Stack traces never reach the client.

### 7.5 Background processing

Celery handles work that must not block a request:

- Material requirement explosion on order confirmation
- Report generation and export
- Document rendering (invoices, packing lists, cut tickets)
- Scheduled tasks: ageing recalculation, reorder-level alerts, backup verification

Tasks are idempotent and carry a correlation ID for tracing.

### 7.6 Configuration

Environment-specific settings come from environment variables, validated at startup. Secrets never enter version control. Business configuration (chart of accounts, defect catalogue, numbering series, AQL tables) lives in the database and is editable by an administrator without a deployment.

### 7.7 Logging and observability

- Structured JSON logging with a request/correlation ID threaded through API calls and Celery tasks.
- Log levels: business events at INFO, recoverable problems at WARNING, unexpected failures at ERROR with full context.
- Health endpoints for database, cache, and worker liveness.
- Slow-query logging enabled in all environments.

## 8. API Design Conventions

- **Base path:** `/api/v1/`. Version in the path; breaking changes require a new version.
- **Resource naming:** plural nouns — `/api/v1/sales-orders/`, `/api/v1/rolls/`.
- **State transitions are sub-resources, not PATCH on a status field** — `POST /api/v1/sales-orders/{id}/confirm/`. This makes the operation auditable and permission-checkable as a distinct action.
- **Pagination:** cursor-based for large transactional lists, offset for small reference lists. Default page size 50, maximum 500.
- **Filtering:** declared filtersets per resource; arbitrary field filtering is not exposed.
- **Errors:** consistent envelope with `code`, `message`, and optional `details`.
- **Idempotency:** state-changing endpoints accept an `Idempotency-Key` header for safe retry.
- **Documentation:** OpenAPI 3 schema generated by drf-spectacular, served at `/api/schema/`.

## 9. Data Layer Notes

Full entity detail is in `03-Data-Model.md`. Architectural points:

- **Soft delete on masters** (`is_active` flag), never on transactions.
- **JSONB** for genuinely variable attributes (material specification, style attributes), with the caveat that anything filtered or reported on regularly is promoted to a real column.
- **Indexing:** every foreign key, plus composite indexes on `(material, location)` for stock balance and `(document_date)` for transactional tables.
- **Partitioning** is not applied at MVP. `StockMovement` and `JournalEntry` are the candidates when volume warrants it; the schema is designed so partitioning by date can be introduced without a rewrite.
- **Decimal, never float,** for all quantities, rates, and money. Quantities to 4 decimal places, money to the currency's minor unit precision.

## 10. Deployment

### 10.1 Environments

| Environment | Purpose |
|-------------|---------|
| Local | Docker Compose — Django, PostgreSQL, Redis, Celery worker |
| Staging | Production-equivalent; migration rehearsal and UAT |
| Production | Live factory operation |

### 10.2 Runtime topology

```
[Reverse proxy / TLS]
        │
   [Gunicorn — Django app, N workers]
        │
   ├── [PostgreSQL — primary, with streaming replica]
   ├── [Redis — cache, broker]
   └── [Celery workers + beat scheduler]
```

Static files served by the reverse proxy. Media and generated documents on object storage or a mounted volume, depending on the hosting decision (see PRD Open Question Q4).

### 10.3 Release process

1. Migrations are backward-compatible — additive first, destructive changes only after the code no longer references the old shape.
2. CI runs linting, type checks, and the full test suite on every pull request.
3. Deployment: migrate, then release application code, then restart workers.
4. Rollback: application code reverts to the previous image; migrations are not auto-reverted, which is why they must be backward-compatible.

### 10.4 Backup and recovery

- Automated nightly full backup plus continuous WAL archiving.
- Restore procedure documented and rehearsed quarterly; an untested backup is not a backup.
- Retention: 30 daily, 12 monthly.

## 11. Testing Strategy

| Level | Coverage |
|-------|----------|
| Unit | Service-layer business rules — consumption calculation, valuation, AQL decision logic |
| Integration | API endpoints against a real PostgreSQL instance, including permission enforcement |
| Concurrency | Explicit tests for simultaneous reservation and issue against the same stock |
| Data integrity | Constraint violations are tested, not assumed |
| End-to-end | The full order lifecycle described in PRD §10, as an automated scenario test |

Financial and stock calculation code requires test coverage before merge. This is the area where a silent bug is most expensive and least visible.

## 12. Security Considerations

- All traffic over TLS.
- Password hashing via Django's default (Argon2 recommended in settings).
- Rate limiting on authentication endpoints.
- SQL injection prevented by exclusive use of the ORM and parameterised queries; raw SQL requires review.
- File uploads validated by type and size, stored outside the web root, served through an authorising view.
- Dependency vulnerability scanning in CI.
- Personally identifiable data limited to what business operation requires.

## 13. Architectural Risks

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Roll-level tracking creates high row volume | Query performance degrades | Index design, materialised balances, partitioning path reserved |
| Costing logic complexity underestimated | Schedule slip, incorrect margins | Build costing against a real historical order early as a validation case |
| Concurrency bugs in stock allocation | Overselling, phantom shortages | Explicit locking strategy, dedicated concurrency test suite |
| Scope creep from excluded modules (CRM, HR) | Delivery delay | Non-goals stated in PRD §3.2; changes require documented scope revision |
| Single-plant assumption baked into schema | Costly rework at V1 | Location foreign key present from day one, even with one location |

## 14. Decisions Deferred

Recorded so they are not silently made by default:

- Hosting model — on-premises versus cloud (PRD Q4)
- Whether finance is the statutory book of record (PRD Q6)
- Reporting approach — direct queries versus a separate read store
- Barcode hardware and integration protocol
- Whether subcontracting ships in MVP (PRD Q2)

Each of these should be resolved with a short Architecture Decision Record when the time comes.

---

## Revision History

| Version | Date | Author | Change |
|---------|------|--------|--------|
| v0.1 | 23 Jul 2026 | Rupesh | Initial draft |
