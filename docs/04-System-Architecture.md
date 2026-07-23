# System Architecture — Textile & Apparel ERP

**Document:** 04 — System Architecture
**Status:** Draft v0.1
**Owner:** Rupesh
**Last updated:** 23 July 2026
**Related:** `01-PRD-textile-erp.md`, `02-Technical-Architecture.md`, `03-Data-Model.md`

---

## 1. Purpose

Document 02 describes technology choices and coding structure. This document describes the **system as a running whole** — what components exist at runtime, how requests move through them, how modules depend on one another, and where the system fails when something breaks.

Read this to understand how the pieces fit. Read 02 to understand how to build them.

## 2. Architectural Style

**Layered modular monolith.**

One deployable application, internally divided into modules with enforced dependency direction, running behind a reverse proxy with a relational database, a cache/broker, and asynchronous workers.

**Why not microservices:** at a single-plant scale with tightly coupled transactional data — a stock issue that must post a journal entry in the same transaction — distributed transactions would introduce failure modes the business cannot absorb. A cut plan that half-commits is worse than one that fails outright. Service extraction remains available later; the module boundaries defined here are the seams along which it would happen.

**Why layered:** the layering rule (views → services → models) is what keeps business logic testable and prevents the ORM from leaking into HTTP handling. Every module obeys the same internal shape, so a developer who learns one module can navigate any of them.

## 3. Runtime Components

| Component | Responsibility | Scaling |
|-----------|----------------|---------|
| Reverse proxy | TLS termination, static file serving, request rate limiting | Vertical; single instance sufficient at MVP |
| Application server (Gunicorn + Django) | Request handling, business logic, transaction management | Horizontal — add worker processes, then instances |
| PostgreSQL primary | All transactional reads and writes | Vertical; read replica for reporting |
| PostgreSQL replica | Report queries, backup source | Added when reporting load affects transaction latency |
| Redis | Session store, cache, Celery broker | Vertical; single instance at MVP |
| Celery workers | MRP explosion, document rendering, exports, scheduled jobs | Horizontal — add worker processes |
| Celery beat | Scheduled task dispatch (ageing, reorder alerts, backup checks) | Exactly one instance; never run two |
| Object storage / volume | Generated documents, attachments, tech packs | Depends on hosting decision (PRD Q4) |

### 3.1 Layer diagram

```
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│   Web UI     │  │   Barcode    │  │ Integrations │
│   browser    │  │   scanner    │  │   external   │
└──────┬───────┘  └──────┬───────┘  └──────┬───────┘
       │                 │                 │
       └─────────────────┼─────────────────┘
                         │  HTTPS
              ┌──────────▼──────────────────────┐
              │  Reverse proxy                  │
              │  TLS · static · rate limiting   │
              └──────────┬──────────────────────┘
                         │
   ┌─────────────────────▼──────────────────────────┐
   │  Django + DRF application                      │
   │  ┌──────────────────────────────────────────┐  │
   │  │  API layer                               │  │
   │  │  views · serialisers · permissions       │  │
   │  └──────────────────┬───────────────────────┘  │
   │  ┌──────────────────▼───────────────────────┐  │
   │  │  Service layer                           │  │
   │  │  business rules · transactions · locking │  │
   │  └──────────────────┬───────────────────────┘  │
   │  ┌──────────────────▼───────────────────────┐  │
   │  │  Domain models and constraints           │  │
   │  └──────────────────┬───────────────────────┘  │
   └─────────────────────┼──────────────────────────┘
                         │
        ┌────────────────┴────────────────┐
        │                                 │
┌───────▼────────┐              ┌─────────▼────────┐
│  PostgreSQL    │              │  Redis           │
│  ledger        │              │  cache · broker  │
│  masters       │              └─────────┬────────┘
│  + replica     │                        │
└────────────────┘              ┌─────────▼────────┐
                                │  Celery workers  │
                                │  + beat          │
                                └──────────────────┘
```

### 3.2 Layer responsibilities

**API layer** — HTTP only. Parses requests, checks permissions, calls a service, serialises the result, maps domain exceptions to status codes. It contains no business rules and performs no writes directly.

**Service layer** — the only place state changes. Owns transaction boundaries, acquires locks, enforces business invariants, emits domain events. Knows nothing about HTTP; a service can be called equally from a view, a Celery task, or a management command.

**Domain models** — structure, relationships, and database constraints. Model methods compute and validate; they do not orchestrate multi-entity operations.

**Data layer** — PostgreSQL holds truth. Redis holds nothing that cannot be rebuilt.

## 4. Module Dependency Structure

Modules are stacked in tiers. **A module may depend on any tier below it and none above.** Where an upper tier must notify a lower one, it emits a domain event rather than importing upward.

```
                    ┌───────────┐
                    │ Reporting │
                    └─────┬─────┘
          ┌───────────────┼───────────────┐
     ┌────▼────┐    ┌─────▼────┐    ┌─────▼────┐
     │ Finance │    │ Costing  │    │ Quality  │
     └────┬────┘    └─────┬────┘    └─────┬────┘
          └───────┬───────┴───────┬───────┘
            ┌─────▼──────┐  ┌─────▼─────┐
            │ Production │  │   Sales   │
            └─────┬──────┘  └─────┬─────┘
                  └───────┬───────┘
                  ┌───────▼───────┐
                  │  Procurement  │
                  └───────┬───────┘
                  ┌───────▼───────┐
                  │   Inventory   │
                  │  roll ledger  │
                  └───────┬───────┘
                  ┌───────▼───────┐
                  │    Masters    │
                  └───────┬───────┘
                  ┌───────▼───────┐
                  │     Core      │
                  └───────────────┘
```

**Why inventory sits so low:** almost everything above it either consumes stock or values it. Placing it beneath procurement, sales, and production means the stock ledger has no knowledge of why a movement happened — it only records that it did, with a polymorphic reference back to the originating document. That ignorance is what keeps it reusable and hard to corrupt.

**Why reporting sits at the top:** it reads across every module and writes to none. Read-only position at the apex means a reporting change can never break a transaction.

### 4.1 Enforcement

Boundaries are enforced by convention plus an import-linter rule in CI. A pull request that imports `finance` from `inventory` fails the build. Convention without enforcement decays within months.

## 5. Request Lifecycle

A representative write — confirming a sales order:

```
1. POST /api/v1/sales-orders/{id}/confirm/
2. Reverse proxy → application server
3. Authentication: session or JWT resolved to a user
4. Permission check: sales.confirm_order held by user's roles
5. View calls sales.services.confirm_order(order_id, actor)
6. Service opens a database transaction
     ├── validate order state is DRAFT
     ├── validate line quantities match size breakdowns
     ├── allocate order number from NumberSeries (row lock)
     ├── explode BOM → material requirements
     ├── lock affected StockBalance rows (SELECT FOR UPDATE)
     ├── create StockReservation records
     ├── write SalesOrderStatusHistory
     └── commit
7. Service emits OrderConfirmed domain event
8. Event handler queues Celery task: shortage report generation
9. View serialises the order, returns 200
```

Two properties matter here. First, **everything that must be consistent is inside one transaction** — the reservation cannot exist without the status change. Second, **everything that can be eventual is outside it** — the shortage report is queued, not computed inline, so a slow report never blocks an order confirmation.

## 6. Concurrency Model

The riskiest operations in this system are concurrent claims on the same stock. Two planners allocating the same roll at the same moment must not both succeed.

| Operation | Strategy |
|-----------|----------|
| Stock reservation | Pessimistic row lock on `StockBalance` for the transaction duration |
| Roll allocation to a cut plan | Pessimistic lock on the `FabricRoll` row |
| Document number allocation | Pessimistic lock on `NumberSeries`, held briefly |
| Master data edit | Optimistic version column; conflicting save returns a clear error |
| Document header edit | Optimistic version column |
| Journal posting | Transaction-scoped; balanced-entry constraint at database level |

**Lock ordering:** where a transaction must lock multiple stock rows, they are locked in ascending primary-key order. Consistent ordering is what prevents deadlock between two transactions touching the same set of rows in different sequences.

**Lock duration:** locks are held only within the service call, never across a user interaction. There is no "edit lock" on a document that a user could hold indefinitely.

## 7. Transaction and Consistency Boundaries

**Strong consistency (single transaction, non-negotiable):**
- Stock movement and the balance it updates
- Financial posting and its journal entry lines
- Document number allocation and the document using it
- GRN acceptance and the rolls or lots it creates
- Cut plan consumption and the roll lengths it decrements

**Eventual consistency (queued, retriable):**
- Shortage reports after order confirmation
- Document rendering — invoices, packing lists, cut tickets
- Export generation
- Ageing recalculation
- Notification dispatch

The dividing line: if a user could make a wrong decision from stale data, it is strong. If they would merely wait, it is eventual.

## 8. Failure Behaviour

Designing what happens when a component dies is part of the architecture, not an operational afterthought.

| Failure | System behaviour | Recovery |
|---------|------------------|----------|
| Database unavailable | Total outage; application returns 503 | Failover to replica; restore from backup if needed |
| Redis unavailable | Sessions lost (re-login required); background tasks queue in memory and are lost | Restart Redis; tasks are idempotent and safely re-triggered |
| Celery worker crash | Task returns to the queue | Automatic retry with backoff; alert after three failures |
| Celery beat stopped | Scheduled jobs silently stop running | Monitored by heartbeat; this is the quiet failure to watch for |
| Application instance crash | Proxy routes to remaining instances | Process supervisor restarts |
| Transaction deadlock | Transaction rolls back, error surfaced to user | Consistent lock ordering makes this rare; retry is safe |
| Disk full on database | Writes fail; reads continue | Alert at 80% capacity, not at failure |

**Design consequence:** because Celery tasks are idempotent and carry a correlation ID, a lost queue is an inconvenience rather than a data-integrity event. Nothing that matters lives only in Redis.

## 9. Security Architecture

**Trust boundaries** — three of them:

1. **Internet to reverse proxy** — TLS terminated here; everything beyond is a private network.
2. **Proxy to application** — all requests authenticated; no unauthenticated endpoint except health checks and login.
3. **Application to database** — dedicated credentials with least privilege. The application role cannot alter schema in production; migrations run under a separate role.

**Authorisation** is enforced at the API layer through DRF permission classes and re-checked in services for state-changing operations. The UI hides unavailable actions, but hiding is presentation, not control.

**Object scoping** — a user restricted to one location sees only that location's data, applied through queryset filtering in selectors rather than post-fetch filtering. Filtering after the fetch means the data already left the database, which is the wrong place to make that decision.

**Secrets** come from environment variables, validated at startup. A missing secret fails the boot rather than degrading silently.

## 10. Observability

**Correlation ID** is generated at the proxy, threaded through the request, carried into every log line, and passed to any Celery task the request spawns. One identifier reconstructs a full operation across processes.

**Logged at INFO:** business events — order confirmed, stock issued, invoice posted, inspection recorded. These form an operational narrative independent of the audit tables.

**Logged at WARNING:** recoverable conditions — lock contention, retry, validation rejection, three-way match variance.

**Logged at ERROR:** unexpected failures with full context and stack trace, never surfaced to the client.

**Health endpoints** report database connectivity, Redis connectivity, worker liveness, and beat heartbeat. The beat heartbeat matters most, because a stopped scheduler produces no errors at all — only an absence of work that nobody notices for days.

## 11. Deployment Topology

**MVP — single node:**

```
┌────────────────────────────────────────────┐
│  Application server                        │
│  proxy · Gunicorn · Celery worker · beat   │
│  PostgreSQL · Redis                        │
└────────────────────────────────────────────┘
```

Adequate for a single plant with modest concurrency. Simple to operate, simple to back up.

**Growth path — separated:**

```
┌──────────┐   ┌──────────────────┐   ┌──────────────┐
│  Proxy   │──▶│  App instances   │──▶│  PostgreSQL  │
│          │   │  (N)             │   │  primary     │
└──────────┘   └────────┬─────────┘   │  + replica   │
                        │             └──────────────┘
               ┌────────▼─────────┐   ┌──────────────┐
               │  Celery workers  │──▶│  Redis       │
               │  + beat (one)    │   └──────────────┘
               └──────────────────┘
```

The application is stateless, so horizontal scaling requires no code change — sessions live in Redis, files in object storage.

**Environments:** local (Docker Compose), staging (production-equivalent, used for migration rehearsal), production.

**Release sequence:** migrate → deploy application → restart workers. Migrations must be backward-compatible, because the rollback path reverts code but not schema.

## 12. Extension Points

Places where V1 capabilities attach without structural change:

| Future capability | Attaches at |
|-------------------|-------------|
| Supplier / customer portal | New API namespace with scoped permissions; no new service layer |
| Barcode scanning | Existing REST endpoints; the scanner is another API client |
| Multi-plant | `Location` hierarchy already present; adds scoping to selectors |
| Analytics dashboards | Reporting module against the read replica |
| Accounting package integration | Domain events on journal posting |
| Notifications | Domain events; handler dispatches to email/SMS gateway |

The domain event mechanism carries most of this. Introducing it at MVP — even with only two or three events — is what keeps these additions from requiring surgery on the service layer later.

## 13. Architecture Risks

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Stock ledger row volume growth | Balance queries slow as movements accumulate | Materialised `StockBalance`; date partitioning path reserved on `StockMovement` |
| Lock contention at peak cutting hours | Reservation timeouts, user-visible delays | Short lock duration, consistent ordering, contention monitoring |
| Monolith becomes hard to reason about | Development slows; boundaries erode | CI-enforced import rules; module ownership |
| Reporting queries degrade transaction latency | Factory operations slow during report runs | Read replica; reporting confined to it |
| Celery beat single point of failure | Scheduled work silently stops | Heartbeat monitoring with alerting |
| Single-node MVP deployment | Any component failure is a total outage | Documented and rehearsed restore; growth path defined above |

## 14. Decisions Recorded

| # | Decision | Rationale |
|---|----------|-----------|
| AD-01 | Modular monolith over microservices | Transactional coupling between stock and finance makes distribution costly |
| AD-02 | Stock as append-only ledger with materialised balance | Auditability without sacrificing read performance |
| AD-03 | Pessimistic locking for stock, optimistic for masters | Matches contention profile — stock is contended, masters are not |
| AD-04 | Synchronous transaction, asynchronous side effects | Correctness where it matters, responsiveness elsewhere |
| AD-05 | Domain events for cross-tier notification | Preserves dependency direction; enables later integration |
| AD-06 | Read replica for reporting | Isolates analytical load from factory operations |

---

## Revision History

| Version | Date | Author | Change |
|---------|------|--------|--------|
| v0.1 | 23 Jul 2026 | Rupesh | Initial draft |
