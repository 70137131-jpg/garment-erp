# Improvement Plan

Hardening and remediation plan following the July 2026 review. Ordered so that
data-correctness fixes land before the features that consume their outputs, and
so structural refactors happen while test coverage is still trusted.

Effort figures are rough developer-days for one person familiar with the code.

| Phase | Theme | Effort | Blocks |
|-------|-------|--------|--------|
| 1 | Correctness: idempotency race, pagination | 4–6d | Everything downstream |
| 2 | Guardrails: lint, types, pinning, CI | 2–3d | — |
| 3 | Structural sweep: unit of work + write permissions | 8–12d | Phase 5 (UI needs permission list) |
| 4 | Frontend data layer and resilience | 5–7d | Phase 5 |
| 5 | Test coverage: frontend unit + Playwright E2E | 6–8d | — |
| 6 | Product roadmap (`goal.TXT`) | ongoing | Phases 1–3 |

---

## Phase 1 — Correctness

Highest risk, smallest diff. Nothing else should start until this lands.

### 1.1 Idempotency race (data corruption)

`app/kernel/idempotency.py` does check-then-act. `find_existing` and `record`
are separated by the entire business transaction, and the backing table has no
unique constraint — `6b5281d83d4d_initial_schema.py` creates both indexes with
`unique=False`. Two concurrent replays of the same `client_key` both miss the
lookup, both post, and both commit. Stock is double-posted.

Affected callers: `procurement/router.py` (`create_goods_receipt`) and
`production/router.py` (`record_daily_output`) — precisely the shop-floor
capture endpoints where network retries are routine.

**Work:**

1. New migration on head `d8e4f2a9c6b1`. Add a unique constraint on
   `(scope, client_key)`. Include a pre-flight de-duplication step: existing
   deployments may already hold duplicate rows, and the constraint will refuse
   to build if so. Keep the two existing non-unique indexes or fold the
   `client_key` one into the composite.
2. Invert the protocol in `idempotency.py` to claim-then-work:
   - `INSERT` the key first, with the resource id nullable.
   - On `IntegrityError`, roll back to a savepoint, re-read the winning row and
     short-circuit to its resource.
   - Backfill `resource_id` after the business call succeeds.
   - A claimed-but-unfinished key (the original request crashed mid-flight)
     must not permanently wedge the key. Decide explicitly: either return `409`
     so the client retries, or treat a null `resource_id` older than N minutes
     as reclaimable. Recommend `409` — simpler and safe.
3. Update both call sites to the new helper signature.

**Testing constraint:** the existing `session` fixture shares one connection
with savepoint isolation, so it *cannot* reproduce a race. Add a
`concurrent_client` fixture that builds a real file-backed SQLite or Postgres
engine and issues two requests from separate threads with independent sessions.
Mark it `@pytest.mark.concurrency` and require the Postgres CI leg, since
SQLite serialises writes and will pass vacuously.

**Done when:** two concurrent goods receipts with the same `client_key` produce
exactly one receipt, one stock posting, and one idempotency row — proven on the
Postgres CI leg.

### 1.2 In-memory pagination

`inventory/router.py:190` is `list(reversed(rows))[offset:offset + limit]` —
`query_rolls` materialises every matching roll before Python slices it. Push
ordering and bounds into the query: `order_by(Roll.id.desc()).offset().limit()`.
`query_rolls` must return a statement (or accept bounds) rather than a list.

### 1.3 Unbounded list endpoints

37 `GET` list endpoints have no `page_bounds` call. Full inventory:

- **costing** — `list_sheets`
- **finance** — `list_accounts`, `list_journals`, `list_ar`, `list_ap`
- **inventory** — `valuation_report`, `list_reservations`, `list_operations`
- **masters** — `list_colours`, `list_seasons`, `list_size_ranges`,
  `list_customers`, `list_suppliers`, `list_materials`
- **procurement** — `list_requisitions`, `po_history`, `supplier_performance`,
  `list_goods_receipts`
- **production** — `list_cuts`, `list_sewing_orders`, `list_daily_outputs`,
  `list_subcontracts`, `wip_summary`
- **sales** — `list_shipments`, `material_requirements_for_order`, `order_history`
- **styles** — `list_styles`, `list_colourways`, `list_bom_versions`
- **tna** — `list_templates`, `order_plan`, `board`
- **workforce** — `list_employees`, `day_sheet`, `monthly_summary`,
  `list_piece_rates`, `earnings`

Not all need paging — `list_accounts` is bounded by the chart of accounts, and
`board` is a fixed-window view. Triage and mark those explicitly with a comment
rather than silently leaving them unbounded, so the exemption is a decision
rather than an oversight.

Introduce a generic envelope in `kernel/query.py`:

```python
class Page(SQLModel, Generic[T]):
    items: list[T]
    total: int
    offset: int
    limit: int
```

Returning `total` is what unlocks real server-side paging in the UI (Phase 4).
This changes response shapes, so it is a breaking API change — do it in one
sweep, not incrementally.

### 1.4 N+1 queries

- `procurement/router.py:426` — `session.get(PurchaseOrder, ...).lines` inside a
  response helper.
- `inventory/router.py:436` — `[session.get(Roll, rid) for rid in payload.roll_ids]`.
- `list_goods_receipts` calls `_gr_read` per row.

Move all three into the respective `service.py` with `selectinload` /
`where(...in_(...))`. The codebase already uses `selectinload` correctly in
places, so this is applying an existing pattern.

---

## Phase 2 — Guardrails

Independent of Phase 1; can run in parallel if two people are available.

1. **Ruff** — add `pyproject.toml` with lint + format. Expect a large first
   diff; land the autofix as one isolated commit so it never mixes with logic
   changes. Fixes the import-below-executable-code ordering in `main.py:17-20`.
2. **Mypy** — start non-strict, module-by-module. Enable `strict` for
   `app/kernel/` first; it is the smallest and most depended-upon package.
3. **Pin dependencies** — every line in `requirements.txt` is `>=`, so builds
   are not reproducible and a FastAPI or SQLModel minor release can break
   production without a commit. Move to `uv` with `uv.lock` (or pip-tools with
   `requirements.lock`). Keep the loose file as `requirements.in`.
4. **ESLint + Prettier** for the frontend. Config only; no rule-tuning rabbit
   holes. Note `useAsync.ts` already carries an `eslint-disable` comment for a
   linter that was never installed.
5. **CI additions** — `ruff check`, `ruff format --check`, `mypy`, `eslint`,
   plus `pip-audit` and `npm audit --audit-level=high`. The existing Postgres
   matrix and `upgrade → downgrade → upgrade` migration cycle stay as-is; they
   are already stronger than most projects.
6. **Untrack `frontend/tsconfig.tsbuildinfo`** — `git rm --cached` and add to
   `.gitignore`. It is a build artifact currently showing dirty in every diff.
7. **Fix the broken README link** — it points at `docs/build-plan.md`, which
   does not exist.

---

## Phase 3 — Structural sweep

Two refactors that both touch all 84 write endpoints. Doing them as separate
passes means churning the same lines twice, so run them together, one domain
module at a time.

### 3.1 Unit of work

`session.commit()` appears ~70 times across routers, each paired with a
hand-written `session.rollback()` in an `except`. Every new endpoint is another
chance to forget one, and the guarantee is per-endpoint rather than structural.

Replace with a dependency that commits on success and rolls back on any
exception, wrapping `get_session`. Domain errors (`StockError`,
`ProcurementError`) become exception handlers registered on the app, so routers
raise and stop hand-rolling `rollback` + `HTTPException`.

Care needed: `get_session` is deliberately `async` so the context var reaches
threadpool-run sync endpoints and finance subscribers join the same
transaction. That property must be preserved — it is subtle, documented in
`db.py`, and easy to break.

### 3.2 Write permissions

Read access is permission-gated in `main.py:89-100`; every write is
role-gated via ~90 `require_roles` calls. The consequence is that granting
"storekeeper who may also approve POs" requires a code change and redeploy.

`Permission` currently defines only read permissions plus `users_manage` and
`workforce_read`. So this is a design task, not a mechanical one:

1. Define write permissions per domain — roughly `<domain>:create`,
   `:approve`, `:post`, `:cancel`. Expect 30–40 members. Derive them from what
   the 84 write endpoints actually gate on today, not from a taxonomy invented
   up front.
2. Extend `ROLE_PERMISSIONS` so each existing role's effective access is
   **exactly** what it is today. This must be behaviour-preserving.
3. Swap `require_roles` → `require_permissions` per module.
4. `test_rbac_audit.py` must be extended to assert the before/after equivalence
   role by role — this is the safety net for the whole phase.
5. Segregation-of-duties rules currently expressed as role checks need to
   survive as permission checks; check `security/service.py` for these.

Optional follow-up once permissions are data-driven: custom roles editable in
the Users screen. That is the customer-visible payoff, and it is worth doing
only after 3.2 is proven stable.

---

## Phase 4 — Frontend data layer

1. **TanStack Query** replaces `lib/useAsync.ts`. The current hook is a tidy 35
   lines and correctly guards setState-after-unmount, but has no cache, no
   request deduplication, no `AbortController`, and every mutation triggers a
   manual full `reload()`. Migrate page by page; the hook can coexist during
   the transition.
2. **Error boundaries** around the lazy routes in `App.tsx`. One page-level
   throw currently blanks the entire app.
3. **Consume server pagination** from Phase 1.3 — replace client-side slicing in
   `ListTools.tsx` and the register pages with real offset/limit + `total`.
4. **Permission-aware UI** — once Phase 3.2 lands, drive nav and action-button
   visibility from `AuthUser.permissions` rather than role name checks.
5. **Accessibility pass** on the register tables and forms — keyboard
   navigation, focus management on modals, labelled controls. Cheapest while
   the components are already being edited.

---

## Phase 5 — Test coverage

Currently: 2,847 lines of backend tests and **zero** frontend or E2E tests
against 4,236 lines of TypeScript.

1. **Vitest + Testing Library** for `api/client.ts` (CSRF retry path, 401
   dispatch), `lib/format.ts`, and the `ui.tsx` primitives.
2. **Playwright E2E**, sequenced after Phase 4 so the UI is not still moving:
   - Full spine: login → confirm SO → PO → receive → QC → cut → sew → inspect
     → ship → invoice. This mirrors `test_acceptance_partc.py` at the browser
     level.
   - One test per role asserting nav and action visibility matches permissions.
   - Session expiry and forced password change.
3. **Concurrency suite** — extend the Phase 1.1 fixture to cover concurrent
   reservations and concurrent stock movements against Postgres. `goal.TXT`
   already calls for this.
4. **Restore drill** — the compose stack has a `backup` service; add a CI job or
   documented runbook that proves a backup actually restores.

---

## Phase 6 — Product roadmap

The sequence in `goal.TXT` is sound, with two adjustments:

1. **Phase 1.1 comes first, before all of it.** Actual costing and three-way
   match both consume stock and GRN quantities. Double-posted receipts corrupt
   their inputs and surface months later as unexplainable cost variances.
2. **Supplier invoices + three-way match moves ahead of actual costing.**
   Matching delivers AP correctness with less new machinery, and it produces
   the verified invoice amounts that actual costing wants as an input anyway.

Resulting order:

1. Partial shipment
2. Lock-safe inventory + valuation
3. Supplier invoices + three-way match *(moved up)*
4. Actual costing + COGS + variance
5. Tax codes and transaction-date exchange rates
6. Production planning (work orders, cut plans, capacity calendar, planning board)
7. Subcontract inventory and external WIP
8. Quality depth (defect catalogue, AQL major/minor, rework workflow)
9. Master-data completeness (delivery addresses, trim lots, configurable locations)
10. Material shortage / requisition / consolidation screens — backend already
    supports these; only the UI is missing, so this is the cheapest visible win
    and can be pulled forward whenever there is a gap.

---

## Sequencing notes

- Phases 1 and 2 are independent and parallelisable.
- Phase 3 is the riskiest work in this plan. It should not start until Phase 2's
  linting and type checking are in CI, and it must land in small per-module PRs
  rather than one sweep.
- Phase 5's E2E tests are wasted effort before Phase 4 settles the UI.
- Phase 1.3 and Phase 3.2 are both breaking API changes. If there are external
  API consumers, batch them into a single versioned release.
