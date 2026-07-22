# Garment Manufacturing ERP — Architecture & Functional Blueprint

*What we are building, what it is made of, and every module and feature in detail.*

---

## PART A — SYSTEM ARCHITECTURE

### A1. What this system is
A single source of truth for a garment manufacturing business. Every transaction entered once, in one place, automatically updates every department that depends on it. Built for the realities of apparel manufacturing: one style exists as many sellable variants (colour × size), fabric is tracked roll by roll and dye-lot by dye-lot, consumption varies by size, production loses material at every stage, and profitability is only known once the order is fully shipped and costed.

### A2. Architectural layers
1. **Presentation** — back-office web UI, shop-floor screens, inspection screens; later supplier/customer portals and dashboards.
2. **Application** — the nine functional modules; business rules live here.
3. **Services** — business logic that fires on events, separated from screens.
4. **Data** — one database: master data, transactions, documents, audit log. Nothing deleted; corrections are new entries.
5. **Integration** — APIs for frontend, scanners, external systems; later barcode/QR, portals, export docs, accounting exports.

### A3. Core architectural principles
- **Single entry, universal effect.**
- **Immutable stock ledger** — append-only; balance = sum of lines.
- **Roll- and lot-level identity** — every roll carries dye lot, shade group, length, width, GSM, grade, location.
- **Version control on specifications** — BOMs, cost sheets, recipes versioned; approving a revision never overwrites history.
- **Controlled, sequential document numbering** — gap-free, auto-generated.
- **Segregation of duties.**
- **Status-driven workflow** — actions permitted only in the correct state.

### A4. Deployment shape
Cloud-hosted core; shop-floor capture must tolerate brief outages and sync when reconnected. Hybrid (cloud core, resilient local capture) is the V1 target.

---

## PART B — THE MODULES

### MODULE 1 — MASTER DATA
Foundation everything builds on. Defines *what things are*.
- **1.1 Customer management** — identity, currency, terms, credit limit, billing, contacts; deactivate never delete.
- **1.2 Supplier management** — split by supply type (fabric/trims/packaging/chemicals/services); currency, terms, lead time, compliance flags; approved per material type.
- **1.3 Material master** — every purchasable/storable/consumable item; type, UoM, lot-tracked flag, lead time, MOQ; fabrics carry composition/construction/weave/GSM/width; dual UoM (buy by roll, store/issue by metre) with conversion.
- **1.4 Colour library** — code, name, Pantone, hex.
- **1.5 Season & calendar** — seasons with date windows.
- **1.6 Size range definitions** — named, *ordered* size sets.
- **1.7 Style master** — number, description, customer, season, gender, category, size range, lifecycle status, standard sewing time, notes.
- **1.8 Style colourways** — approved colour variants with buyer reference and lab-dip approval flag.
- **1.9 Bill of Materials** — recipe with **per-size consumption**, wastage %, optional colour link, optional-component flag; **versioned**.
- **1.10 Version control** across BOMs, cost sheets, recipes.

### MODULE 2 — SALES ORDERS
- **2.1 Header** — auto number, customer, PO number, season, date, currency, Incoterms, terms, status.
- **2.2 Lines by style & colour** — price, delivery date, destination, packing ratio.
- **2.3 The size matrix** — grid of colours × sizes; one tracked cell per size (ordered→confirmed→shipped). Hard requirement.
- **2.4 Confirmation & status flow.**
- **2.5 Amendments** with trail.
- **2.6 Credit & pricing checks.**
- **2.7 Available-to-promise** — data in MVP, logic in V1.

### MODULE 3 — PROCUREMENT
- **3.1 PO management** — auto number, supplier, optional SO link, status; lines track received vs ordered.
- **3.2 Fabric/trim-specific ordering** — construction, composition, colour, width, GSM, finish, tolerance, inspection standard.
- **3.3 Goods receipt** — pivotal event: updates PO, advances status, moves stock, generates roll records.
- **3.4 Roll creation on receipt** — each roll a distinct unit with roll number, dye lot, shade code/group, length, weight, width, GSM, grade, location.
- **3.5 Partial & over/under receipt.**
- **3.6 Supplier performance** — data in MVP, scored in V1.

### MODULE 4 — INVENTORY & WAREHOUSE
- **4.1 Stock ledger** — immutable, append-only.
- **4.2 Detailed stock identity** — warehouse, bin, lot, roll, shade, grade, width, GSM, length, weight, quality/reservation status, ownership.
- **4.3 Roll register & queries** — find rolls by shade group, width, grade.
- **4.4 Reservations** — lifecycle Active→Released→Consumed.
- **4.5 Warehouse transactions** — receive, hold, put-away, reserve, pick, issue, return, transfer, repack, split/join, re-grade, cycle count, dispatch, returns.
- **4.6 Roll selection logic** — prioritise shade-group → width/grade → qty fit → oldest → customer restriction.

### MODULE 5 — PRODUCTION
- **5.1 Cut order** — style/colour, size breakdown, marker efficiency, fabric required/issued, pieces cut; status Planned→Fabric Reserved→In Cutting→Completed.
- **5.2 Fabric requirement calculation** — per-size consumption × qty + wastage.
- **5.3 Roll issue to cutting** — partial issue & return; posts stock movements.
- **5.4 Sewing order** — assigned to a line; status Planned→Active→Completed→Closed.
- **5.5 Daily output & efficiency** — per order/day/shift; SAM-based line efficiency.
- **5.6 Subcontracting** — sent/expected/received/rate/dates; outstanding balance; full reconciliation in V1.
- **5.7 Full production route** — progressive; MVP covers cut-to-sew-to-output spine.

### MODULE 6 — COSTING
- **6.1 Style cost sheet** — versioned, base size, currency; material + conversion + overhead → margin → price.
- **6.2 Cost line detail** — categorised lines with qty/rate/wastage.
- **6.3 Material cost** — qty (incl. waste/shrinkage/rejection/size-width) × rate.
- **6.4 Sewing cost via SAM** — SAM × cost/min ÷ efficiency.
- **6.5 Selling price & margin.**
- **6.6 Standard vs actual variance** — framework in MVP, full in V1.
- **6.7 Order profitability.**

### MODULE 7 — QUALITY
- **7.1 Incoming fabric inspection (four-point).**
- **7.2 Defect logging** with standardised penalty points.
- **7.3 Inline inspection** — DHU.
- **7.4 Final inspection (AQL).**
- **7.5 Quality status on inventory.**
- **7.6 Lab-dip / recipe approval linkage** — colourway level in MVP, full lab in V1.

### MODULE 8 — FINANCE
- **8.1 Chart of accounts** — hierarchical.
- **8.2 Journals & GL** — double-entry, manual/system, Draft→Posted→Reversed.
- **8.3 Automatic accounting integration** — operational events post their own accounting.
- **8.4 Accounts receivable.**
- **8.5 Accounts payable.**
- **8.6 Profitability analysis** — by customer/order/style/season/factory.
- **8.7 Multi-currency & consolidation** — V1.

### MODULE 9 — ACCESS CONTROL & SECURITY (cross-cutting)
- **9.1 Role-based access.**
- **9.2 Segregation of duties.**
- **9.3 Approval workflows.**
- **9.4 Audit trail.**

---

## PART C — END-TO-END ORDER FLOW (master acceptance scenario)
1. **Masters** — TS-100 exists, approved Navy/White, size range, versioned per-size BOM, approved cost sheet.
2. **Sales Orders** — order entered as size matrix (2 colours × 8 sizes); confirmation signals demand.
3. **Procurement** — fabric purchased; goods receipt posted; each roll a uniquely identified unit.
4. **Quality** — incoming rolls four-point inspected; approved available, failed quarantined.
5. **Inventory** — matching rolls reserved for the cut order.
6. **Production** — cut order calculates fabric from size-BOM; rolls issued (ledger consumption); pieces cut; sewing runs; daily efficiency recorded; embroidery subcontracted and reconciled.
7. **Quality** — inline DHU; final AQL authorises shipment.
8. **Costing** — actuals feed order profitability vs cost sheet.
9. **Finance** — receipts/issues/production/shipment/invoicing post automatically; AR invoice issued; final P&L reported.

---

## PART D — MVP VS V1
**MVP** — all nine modules connected; versioned size-aware BOMs; size-matrix order entry; roll-level goods receipt; immutable ledger with roll register & reservations; cut/sew orders with fabric calc & efficiency; subcontracting with outstanding balance; versioned cost sheets with SAM & order profitability; four-point/inline/AQL quality; CoA, journals with auto-posting, AR/AP; RBAC, approvals, audit. The order-to-cash spine runs end to end.

**V1** — dedicated frontend, MRP, full variance analysis, subcontract reconciliation, export docs, multi-currency & consolidation, forecasting, sustainability tracking, barcode/QR, portals, full lab management, KPI dashboard, predictive maintenance.
