# Garment ERP — Build Plan (MVP)

Stack: FastAPI + SQLModel + Alembic. SQLite for local dev, **Postgres in
production** (the immutable ledger, gap-free numbering, reservations, and
concurrent shop-floor writes need real transactions / sequences / row locks).

## Reordering vs the blueprint

Two functional dependencies override the blueprint's 1→9 numbering:

1. **The stock ledger (Module 4) is a kernel, not a mid-build module.** Goods
   receipt, roll creation, reservations and production issue all *write to the
   ledger*. Build the ledger primitive right after Master Data.
2. **Finance (Module 8) is a subscriber, not "last".** Auto-posting consumes
   events every other module emits. Introduce a domain-event seam on day one;
   wire posting rules late.

Real build order: **Kernel → Masters → Ledger → Sales → Procurement (+incoming
QC) → Reservations → Production → Costing → Finance → Harden.**

## Phase 0 — Kernel (before any module)

Document numbering · state machine · immutable-ledger pattern · versioned
specification · audit trail · domain-event seam · RBAC skeleton.

Done when: a dummy document can be numbered, walked through a state machine with
role checks, and leaves an audit row — all in one transaction.

## Phase 1 — Master Data (Module 1)

Colour / Season / Size range → Customer / Supplier → Material master (dual UoM)
→ Style + Colourways → **BOM with per-size consumption, versioned**.

Key risk: the per-size BOM shape (`bom_version → bom_line →
bom_line_size_consumption`) drives fabric-requirement calc and material costing.
Get it right here.

## Phase 2 — Stock ledger + roll register (Module 4 core, pulled early)

Append-only movements with full identity; balance = sum of lines. Roll identity
queryable by material / dye-lot / shade-group / grade / width / status.

## Phase 3 — Sales orders (Module 2)

Header + lines by style×colour + **size matrix stored as normalized per-size
cells** (ordered→confirmed→shipped). Confirmation emits `SalesOrderConfirmed`.

## Phase 4 — Procurement + incoming quality (Modules 3 + 7.1)

Goods receipt is the pivotal event: in one transaction it updates the PO, posts
ledger movements, creates a roll per physical roll, and emits
`GoodsReceiptPosted`. Four-point inspection gates roll status
(approved→available, failed→quarantined).

## Phase 5 — Reservations + roll selection (Module 4 rest)

Reservation lifecycle (Active→Released→Consumed). Roll selection prioritises
shade-group → width/grade → qty fit → oldest → customer restriction.

## Phase 6 — Production (Module 5) + subcontracting

Cut order → **fabric requirement from size-BOM** → roll issue (ledger
consumption) → sewing orders → daily output & SAM-based efficiency → emits
`ProductionConfirmed`. Subcontracting with outstanding-balance tracking.

## Phase 7 — Inline + AQL quality (7.3–7.4)

DHU inline; AQL final gates shipment.

## Phase 8 — Costing (Module 6)

Versioned cost sheet (material + SAM sewing + overhead → margin → price). Order
profitability fed by operational events.

## Phase 9 — Finance (Module 8)

CoA · double-entry journals · **auto-posting as an event subscriber** · AR/AP ·
profitability analysis.

## Phase 10 — Harden (Module 9 + acceptance)

Full RBAC · segregation of duties · approval workflows · audit completeness ·
**run the Part C end-to-end scenario (TS-100 masters→P&L) as the MVP
definition-of-done.**

## Locked-in decisions

- Size matrix = normalized per-size cells (queryable), never JSON.
- Versioning = header + immutable version rows; downstream FKs to a specific
  version id, never to the style.
- Money/quantities = `Decimal` with defined precision. No floats near cost/stock.
- Events = simple synchronous dispatcher for MVP; the point is the *seam*.
- Shop-floor capture endpoints idempotent (client keys) so V1 offline sync
  doesn't require rework.

## Honest sizing

As scoped this is several months of focused solo work — nine interlocking
modules with real integrity guarantees, not CRUD. Each phase is independently
demoable. Fastest narrowing: one fabric type, one customer, one style, single
currency, online-only — prove the Part C spine, then widen.
