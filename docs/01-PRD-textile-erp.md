# Product Requirements Document — Textile & Apparel ERP

**Document:** 01 — PRD
**Status:** Draft v0.1
**Owner:** Rupesh
**Last updated:** 23 July 2026

---

## 1. Purpose

This document defines the scope, requirements, and success criteria for an ERP system purpose-built for garment and apparel manufacturing. It is the authoritative reference for what the system must do. It does not describe how the system is built — see `02-Technical-Architecture.md`.

## 2. Problem Statement

Garment manufacturers operate on thin margins, short seasons, and high SKU variability. Generic ERP platforms (SAP, Oracle, Odoo) either cost more than a mid-size factory can justify, or require heavy customisation to handle apparel-specific realities:

- Inventory tracked at **roll and lot level**, not just SKU level — a fabric roll has a width, a shade lot, a defect map, and a remnant length.
- Costing driven by **consumption per garment** (fabric CPG, trims, CMT) rather than simple BOM multiplication.
- Production planned around **cut-sew-finish sequences** with size/colour matrices, not discrete unit assembly.
- Quality inspected at **fabric inbound (4-point system)**, in-line, and final AQL stages.

Factories therefore run on spreadsheets, WhatsApp, and paper cutting tickets. Costing is retrospective. Fabric shortages are discovered at the cutting table. Nobody can answer "what did this order actually cost us" until months after shipment.

## 3. Goals and Non-Goals

### 3.1 Goals

| # | Goal | Measure of success |
|---|------|--------------------|
| G1 | Single source of truth for orders, materials, and production | No parallel spreadsheet required for core operations |
| G2 | Roll- and lot-level material traceability | Any finished garment can be traced back to its fabric roll and lot |
| G3 | Pre-production and post-production costing | Estimated vs actual cost variance available per order within 48h of shipment |
| G4 | Material shortage visibility before cutting starts | Shortages flagged at order confirmation, not at cut planning |
| G5 | Structured quality capture | Inbound, in-line, and final inspection results recorded against the order |

### 3.2 Non-Goals (explicitly out of scope)

- **CRM** — no leads, opportunities, pipeline, campaigns, or marketing automation. Customers exist as reference records for orders, invoicing, and credit only.
- **HR and payroll** — no attendance, salary processing, statutory filings, or leave management.
- **E-commerce and retail POS** — no storefront, cart, or in-store checkout.
- **Mill processes** — no spinning, weaving, knitting, or dye-house production management. Fabric is purchased, not manufactured.
- **Warehouse robotics / WMS automation** — no putaway optimisation, wave picking, or conveyor integration.

## 4. Users and Roles

| Role | Primary needs |
|------|---------------|
| Merchandiser | Create and track sales orders, styles, and delivery dates; monitor order status |
| Procurement Officer | Raise purchase orders, track supplier deliveries, manage supplier records |
| Store Keeper | Receive goods, issue material to production, record stock movements |
| Production Planner | Build cut plans, allocate orders to lines, sequence work orders |
| QC Inspector | Record inbound fabric inspection, in-line checks, final AQL results |
| Costing Analyst | Build cost sheets, compare estimated vs actual, review margins |
| Finance Officer | Raise invoices, record payments, view receivables and payables |
| System Administrator | Manage users, roles, master data, and configuration |

Access is role-based. A user may hold multiple roles.

## 5. Scope — MVP

The MVP delivers six modules. Each is described here by capability; entity detail lives in `03-Data-Model.md`.

### 5.1 Sales Orders

**Purpose:** Capture what the customer ordered and track it to shipment.

Requirements:

- **SO-01** Create a sales order against a customer, with order reference, currency, and delivery date.
- **SO-02** Each order contains one or more **style lines**. A style line references a style master and carries a size/colour breakdown matrix (quantity per size per colour).
- **SO-03** Support order revisions with version history — quantity, price, and delivery date changes must be auditable.
- **SO-04** Order status lifecycle: Draft → Confirmed → In Production → Partially Shipped → Shipped → Closed. Cancellation permitted from any pre-shipment state.
- **SO-05** On confirmation, trigger a material requirement calculation against the style BOM and flag shortages.
- **SO-06** Record shipment against order lines, supporting partial shipments.
- **SO-07** Customer master: name, billing and delivery addresses, payment terms, credit limit, currency. Reference-only — no sales pipeline data.

### 5.2 Procurement

**Purpose:** Buy fabric, trims, and services against known requirements.

Requirements:

- **PR-01** Supplier master: name, addresses, payment terms, lead time, currency, material categories supplied.
- **PR-02** Generate a purchase requisition from a sales order's material shortage, or manually.
- **PR-03** Convert requisitions to purchase orders, with the ability to consolidate multiple requisitions to one supplier.
- **PR-04** Purchase order lines carry material, specification, quantity, unit, rate, currency, and expected delivery date.
- **PR-05** PO status lifecycle: Draft → Sent → Partially Received → Received → Closed. Support amendment with version history.
- **PR-06** Goods Receipt Note (GRN) against a PO, recording actual received quantity, rolls/lots, and inspection outcome.
- **PR-07** Three-way match: PO ↔ GRN ↔ supplier invoice, with variance flagging on quantity and rate.

### 5.3 Inventory (roll and lot level)

**Purpose:** Know exactly what material exists, where, and in what condition.

Requirements:

- **IN-01** Material master covering fabric, trims, accessories, and packaging, with unit of measure and specification attributes.
- **IN-02** Fabric stock tracked at **roll level**: roll ID, lot/shade number, width, length received, length remaining, GSM, supplier, GRN reference, defect points.
- **IN-03** Trim and accessory stock tracked at **lot level**: lot ID, quantity, supplier, GRN reference.
- **IN-04** Multi-location stock — store, floor, cutting section, subcontractor.
- **IN-05** Stock movements: receipt, issue to production, return from production, transfer between locations, adjustment, scrap. Every movement is immutable and timestamped with the acting user.
- **IN-06** Material reservation against a confirmed sales order, preventing double-allocation.
- **IN-07** Remnant tracking — when a roll is partially consumed, the balance remains available with its history intact.
- **IN-08** Stock valuation using weighted average cost, with FIFO as a configurable alternative.
- **IN-09** Physical stock count entry with variance report and adjustment posting.

### 5.4 Costing and Finance

**Purpose:** Know what an order should cost, and what it did cost.

Requirements:

- **CO-01** Pre-production cost sheet per style: fabric consumption per garment, trim costs, CMT, overhead allocation, freight, margin. Produces a target FOB price.
- **CO-02** Actual cost capture per order: material issued (valued at cost), labour hours booked, subcontract charges, rework cost.
- **CO-03** Estimated vs actual variance report at order and style level, broken down by cost head.
- **CO-04** Sales invoice generation against shipment, in order currency, with tax handling.
- **CO-05** Purchase invoice recording against GRN, with three-way match variance visible.
- **CO-06** Accounts receivable and payable ageing reports.
- **CO-07** Payment recording against invoices, supporting partial payment and multi-invoice settlement.
- **CO-08** General ledger postings for inventory, COGS, receivables, and payables. Chart of accounts is configurable.

*Note: this is factory-grade finance, not a full statutory accounting suite. Statutory filing and audit-grade closing are deferred to V1 scope review.*

### 5.5 Production Planning

**Purpose:** Turn confirmed orders into executable factory work.

Requirements:

- **PP-01** Style master: style code, description, season, customer, size set, colourways, BOM, and operation sequence.
- **PP-02** Bill of Materials per style with consumption per garment, wastage percentage, and size-dependent consumption where applicable.
- **PP-03** Work order generation from a confirmed sales order, split by style, colour, and delivery batch.
- **PP-04** Cut plan: marker length, ply count, lay plan, roll allocation, and expected cut quantity per size.
- **PP-05** Production stages tracked per work order: Cutting → Sewing → Finishing → Packing. Quantity progress recorded at each stage.
- **PP-06** Line and capacity allocation with a daily/weekly production board.
- **PP-07** Subcontracting: issue material to a subcontractor, track work-in-progress held externally, receive finished goods back.
- **PP-08** WIP visibility — how many units sit at each stage for each order at any time.

### 5.6 Quality Management

**Purpose:** Catch defects early and record the evidence.

Requirements:

- **QA-01** Inbound fabric inspection using the **4-point system**: defect points recorded per roll, points-per-100-square-yard calculated, accept/reject decision captured.
- **QA-02** Trim and accessory inbound inspection with pass/fail and defect notes.
- **QA-03** In-line inspection during sewing: checkpoint, sample size, defects found, defect type, corrective action.
- **QA-04** Final inspection using **AQL** sampling: lot size, inspection level, AQL limit, sample size, major/minor defects, accept/reject.
- **QA-05** Defect catalogue — a configurable master list of defect types by stage.
- **QA-06** Rework tracking: quantity sent for rework, reason, outcome, cost impact.
- **QA-07** Quality reports by supplier, by style, by line, and by defect type.

## 6. Scope — V1 (post-MVP)

Deferred to V1, listed here so the MVP architecture accommodates them:

| Module | Capability |
|--------|------------|
| Advanced planning | Finite capacity scheduling, machine-level loading, critical path per order |
| Supplier portal | External login for suppliers to view POs and confirm dispatch |
| Customer portal | External login for buyers to view order status and shipment tracking |
| Barcode / RFID | Barcode-driven roll issue, bundle tracking, and stock count |
| Multi-plant | Multiple factories with inter-plant transfers and consolidated reporting |
| Advanced costing | Activity-based costing, standard cost revaluation, contribution analysis |
| Document management | Tech pack storage, spec sheet versioning, artwork attachments |
| Compliance | Audit trail export, buyer compliance checklists (e.g. social audits) |
| Analytics | Dashboard layer with OTIF, capacity utilisation, defect trend, margin trend |

## 7. Non-Functional Requirements

| # | Requirement |
|---|-------------|
| NFR-01 | **Auditability** — every transactional record carries created-by, created-at, modified-by, modified-at. Stock movements and financial postings are append-only. |
| NFR-02 | **Concurrency** — stock and reservation operations must be safe under concurrent access. No overselling of a reserved roll. |
| NFR-03 | **Performance** — list views return within 2 seconds at 100,000 transactional records; reports within 10 seconds. |
| NFR-04 | **Multi-currency** — orders, POs, and invoices in any currency, with exchange rate captured at transaction date. |
| NFR-05 | **Multi-unit** — support metres, yards, kilograms, pieces, dozens, with conversion factors per material. |
| NFR-06 | **Role-based access** — permissions enforced at API level, not only in the UI. |
| NFR-07 | **Data integrity** — referential integrity enforced at database level; soft delete for masters, never hard delete for transactions. |
| NFR-08 | **Localisation-ready** — date, number, and currency formats configurable; UI strings externalised for future translation. |
| NFR-09 | **Backup and recovery** — daily automated backup with a documented restore procedure and tested RTO. |
| NFR-10 | **API-first** — all functionality exposed through documented REST endpoints; the UI consumes the same API as any integration would. |

## 8. Assumptions

- The factory purchases fabric and trims; it does not spin, weave, knit, or dye in-house.
- Users have reliable internet access on the factory floor, or the system is hosted on-premises.
- Initial deployment targets a single plant. Multi-plant is V1.
- Statutory accounting and tax filing are handled in an external accounting package; this ERP posts to a general ledger but is not the statutory book of record at MVP.
- English is the only interface language at MVP.

## 9. Open Questions

| # | Question | Needed by |
|---|----------|-----------|
| Q1 | Which tax regime(s) must invoicing support at MVP? | Before costing module build |
| Q2 | Is subcontracting in scope for MVP, or deferred? Currently included as PP-07. | Before production planning build |
| Q3 | Expected concurrent user count and record volume in year one? | Before architecture sign-off |
| Q4 | On-premises or cloud hosting? | Before architecture sign-off |
| Q5 | Is there an existing system whose data must be migrated? | Before data model freeze |
| Q6 | Does finance need to be the statutory book of record, or is an external accounting package retained? | Before finance module build |

## 10. Success Criteria

The MVP is considered complete when a real order can be run end to end inside the system: entered as a sales order, exploded into material requirements, procured, received at roll level, inspected, allocated to a cut plan, produced through all stages, inspected at final AQL, shipped, invoiced, and costed — with estimated versus actual variance visible, and no step requiring a spreadsheet.

---

## Revision History

| Version | Date | Author | Change |
|---------|------|--------|--------|
| v0.1 | 23 Jul 2026 | Rupesh | Initial draft |
