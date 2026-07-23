# Data Model — Textile & Apparel ERP

**Document:** 03 — Data Model / ERD
**Status:** Draft v0.1
**Owner:** Rupesh
**Last updated:** 23 July 2026
**Related:** `01-PRD-textile-erp.md`, `02-Technical-Architecture.md`

---

## 1. Purpose

This document defines the logical data model: entities, their significant attributes, relationships, and the constraints that protect data integrity. It is the reference for schema implementation and for anyone reasoning about how information is structured.

Physical concerns (indexes, partitioning, storage) are covered in the Technical Architecture Document §9.

## 2. Conventions

- Every entity has a surrogate primary key `id` (BIGSERIAL).
- Every entity inherits audit fields: `created_by`, `created_at`, `modified_by`, `modified_at`.
- Master entities carry `is_active` (soft delete). Transactional entities are never soft-deleted.
- Quantities are `NUMERIC(18,4)`. Money is `NUMERIC(18,4)` with an accompanying currency code.
- Dates are `DATE`; timestamps are `TIMESTAMPTZ` stored in UTC.
- Foreign keys are `RESTRICT` on delete by default. `CASCADE` is used only for genuine parent-child ownership (order → order line).
- Status fields are constrained enumerations, never free text.

## 3. Entity Overview by Domain

```
CORE / MASTERS
  Company · Location · UnitOfMeasure · Currency · ExchangeRate
  NumberSeries · User · Role · Permission · AuditLog

  Customer · Supplier · Material · MaterialCategory
  Style · StyleColourway · StyleSize · BillOfMaterial · Operation

SALES
  SalesOrder · SalesOrderLine · SizeBreakdown
  SalesOrderRevision · Shipment · ShipmentLine

PROCUREMENT
  PurchaseRequisition · PurchaseRequisitionLine
  PurchaseOrder · PurchaseOrderLine
  GoodsReceiptNote · GRNLine

INVENTORY
  FabricRoll · MaterialLot · StockBalance · StockMovement
  StockReservation · StockCount · StockCountLine

PRODUCTION
  WorkOrder · CutPlan · CutPlanRollAllocation
  ProductionStageProgress · SubcontractIssue · SubcontractReceipt

QUALITY
  DefectType · InboundInspection · InboundInspectionDefect
  InlineInspection · FinalInspection · ReworkRecord

COSTING
  CostSheet · CostSheetLine · ActualCostEntry · CostVariance

FINANCE
  ChartOfAccount · SalesInvoice · SalesInvoiceLine
  PurchaseInvoice · PurchaseInvoiceLine
  Payment · PaymentAllocation · JournalEntry · JournalEntryLine
```

---

## 4. Core and Master Data

### 4.1 Reference entities

**Company** — the operating legal entity. Single row at MVP; present so multi-company is not a rewrite.
`code, name, base_currency, address, tax_identifiers`

**Location** — physical or logical stock-holding place.
`code, name, type (STORE | FLOOR | CUTTING | FINISHING | SUBCONTRACTOR | QUARANTINE), parent_location, is_active`
- Hierarchical via self-referencing `parent_location`.
- Subcontractor locations link to a `Supplier`.

**UnitOfMeasure** — `code, name, category (LENGTH | WEIGHT | COUNT | AREA), base_unit, conversion_factor`
- Conversion is within a category only. Metres to yards is valid; metres to kilograms is not, except through a material-specific factor held on `Material`.

**Currency** — `code (ISO 4217), name, minor_unit_digits`

**ExchangeRate** — `from_currency, to_currency, rate, effective_date`
- Unique on `(from_currency, to_currency, effective_date)`.
- Transactions store the rate they used; they never look it up again retrospectively.

**NumberSeries** — `document_type, prefix, current_number, padding, reset_period, financial_year`
- Unique on `(document_type, financial_year)`.
- Allocation takes a row lock to guarantee gapless, non-duplicate numbers.

### 4.2 Access control

**User** — Django's user model, extended with `employee_code, default_location, is_active`.

**Role** — `code, name, description`
**Permission** — `code (e.g. inventory.issue_stock), module, description`
**RolePermission** — join table
**UserRole** — join table; a user may hold several roles.

**AuditLog** — `entity_type, entity_id, field_name, old_value, new_value, action, actor, timestamp`
- Append-only. Covers master-data changes; transactional history is inherent in the transaction tables themselves.

### 4.3 Business partners

**Customer**
`code, name, billing_address, delivery_address, country, currency, payment_terms_days, credit_limit, tax_identifier, is_active`
- Reference data only. No pipeline, lead, opportunity, or campaign fields — see PRD §3.2.

**Supplier**
`code, name, address, country, currency, payment_terms_days, lead_time_days, tax_identifier, is_active`

**SupplierMaterial** — which supplier supplies which material, with `standard_rate, lead_time_days, minimum_order_quantity`.

### 4.4 Materials

**MaterialCategory** — `code, name, parent_category, type (FABRIC | TRIM | ACCESSORY | PACKAGING)`

**Material**
`code, name, category, base_uom, purchase_uom, purchase_conversion_factor, tracking_mode, specification (JSONB), reorder_level, standard_cost, is_active`

- `tracking_mode` ∈ `ROLL | LOT | NONE` — this single field drives how the inventory module treats the material. Fabric is `ROLL`. Trims are `LOT`. Consumables may be `NONE`.
- `specification` JSONB holds category-varying attributes: composition, GSM, width, construction for fabric; size, colour, material for trims.

> **Design note:** attributes that are filtered or reported on regularly (GSM, width) are promoted to real columns rather than left in JSONB, even though they only apply to fabric. Nullable columns are cheaper than JSONB indexing.

### 4.5 Styles

**Style**
`code, name, customer, season, description, size_set, sample_status, is_active`

**StyleColourway** — `style, colour_code, colour_name`
**StyleSize** — `style, size_code, sequence` (sequence preserves XS→XXL ordering)

**BillOfMaterial**
`style, material, colourway (nullable), consumption_per_garment, uom, wastage_percentage, size_dependent (bool)`
- Where `size_dependent` is true, consumption varies by size; the per-size values live in **BOMSizeConsumption** (`bom, style_size, consumption`).
- Unique on `(style, material, colourway)`.

**Operation** — `style, sequence, operation_name, stage (CUTTING | SEWING | FINISHING | PACKING), standard_minutes`

---

## 5. Sales

### 5.1 SalesOrder

`order_number, customer, order_date, delivery_date, currency, exchange_rate, status, incoterms, payment_terms_days, total_value, remarks`

- `status` ∈ `DRAFT | CONFIRMED | IN_PRODUCTION | PARTIALLY_SHIPPED | SHIPPED | CLOSED | CANCELLED`
- `order_number` unique, allocated from `NumberSeries`.
- Status transitions are recorded in **SalesOrderStatusHistory** (`sales_order, from_status, to_status, changed_by, changed_at, reason`).

### 5.2 SalesOrderLine

`sales_order, line_number, style, colourway, quantity, uom, unit_price, line_value, delivery_date, status`

- Unique on `(sales_order, line_number)`.
- `quantity` is the sum of its size breakdown — enforced by a check at the service layer and validated on confirmation.

### 5.3 SizeBreakdown

`sales_order_line, style_size, quantity`

- Unique on `(sales_order_line, style_size)`.
- This is the size/colour matrix in normalised form: colour lives on the order line, size on the breakdown.

### 5.4 SalesOrderRevision

`sales_order, revision_number, revised_at, revised_by, change_summary, snapshot (JSONB)`

- Every post-confirmation change to quantity, price, or delivery date creates a revision with a full snapshot of the order state before the change.
- Append-only.

### 5.5 Shipment

`shipment_number, sales_order, shipment_date, invoice (nullable), carrier, awb_number, status`

**ShipmentLine** — `shipment, sales_order_line, style_size, quantity, carton_count`

- Partial shipment is the normal case. Shipped quantity per order line is derived by summing shipment lines, never stored as a mutable column on the line.

---

## 6. Procurement

### 6.1 PurchaseRequisition

`requisition_number, requested_by, request_date, required_by_date, source_sales_order (nullable), status`

**PurchaseRequisitionLine** — `requisition, material, quantity, uom, required_by_date, status`

- `source_sales_order` links a requisition back to the shortage that generated it, giving demand traceability.

### 6.2 PurchaseOrder

`po_number, supplier, order_date, expected_delivery_date, currency, exchange_rate, status, incoterms, payment_terms_days, total_value`

- `status` ∈ `DRAFT | SENT | PARTIALLY_RECEIVED | RECEIVED | CLOSED | CANCELLED`
- **PurchaseOrderAmendment** mirrors `SalesOrderRevision` — append-only snapshots.

**PurchaseOrderLine**
`purchase_order, line_number, material, specification_notes, quantity, uom, rate, line_value, expected_delivery_date, requisition_line (nullable)`

- Multiple requisition lines may consolidate into one PO line; the link is held on **RequisitionPOLink** where consolidation occurs.

### 6.3 GoodsReceiptNote

`grn_number, purchase_order, supplier, receipt_date, location, status, supplier_dc_number, remarks`

**GRNLine**
`grn, purchase_order_line, material, received_quantity, accepted_quantity, rejected_quantity, uom, rate`

- `received_quantity = accepted_quantity + rejected_quantity` — database check constraint.
- Accepting a GRN line creates `FabricRoll` or `MaterialLot` records and posts stock movements. Rejected quantity moves to a quarantine location, not out of the system.

---

## 7. Inventory

This is the heart of the model. Two principles govern it (see Architecture §6.1, §6.2): **stock is a ledger**, and **a roll has identity**.

### 7.1 FabricRoll

`roll_number, material, lot_number, grn_line, supplier, width, gsm, received_length, remaining_length, uom, location, shade_group, defect_points, points_per_100_sqyd, status, received_date`

- `roll_number` unique.
- `status` ∈ `AVAILABLE | RESERVED | PARTIALLY_ISSUED | ISSUED | QUARANTINE | REJECTED | SCRAPPED`
- `remaining_length` is maintained inside the same transaction as the stock movement that changes it, never independently.
- Check constraint: `remaining_length >= 0` and `remaining_length <= received_length`.
- `shade_group` allows rolls of the same lot to be grouped for cutting consistency.

> **Why a roll is not just stock:** cutting a garment from two rolls of different shade lots produces visible colour variation across panels. The model must let a planner allocate specific rolls, and let anyone trace a finished garment back to them.

### 7.2 MaterialLot

`lot_number, material, grn_line, supplier, received_quantity, remaining_quantity, uom, location, status, received_date, expiry_date (nullable)`

- The trim and accessory equivalent of `FabricRoll`, without the physical dimensions.
- Unique on `(material, lot_number)`.

### 7.3 StockBalance

`material, location, lot_or_roll_reference (nullable), quantity_on_hand, quantity_reserved, quantity_available, average_cost, last_movement_at`

- A **materialised** view of the movement ledger, updated inside the transaction that posts the movement.
- `quantity_available = quantity_on_hand - quantity_reserved` — a generated column.
- Unique on `(material, location, lot_or_roll_reference)`.
- This is the row that gets locked (`SELECT FOR UPDATE`) during reservation and issue.

### 7.4 StockMovement

`movement_number, movement_type, movement_date, material, from_location, to_location, roll (nullable), lot (nullable), quantity, uom, rate, value, reference_type, reference_id, remarks, posted_by, posted_at`

- `movement_type` ∈ `RECEIPT | ISSUE | RETURN | TRANSFER | ADJUSTMENT | SCRAP | SUBCONTRACT_ISSUE | SUBCONTRACT_RECEIPT`
- **Append-only. No UPDATE, no DELETE.** Corrections post a reversing movement carrying `reverses_movement_id`.
- `reference_type` / `reference_id` form a polymorphic link to the originating document (GRN, work order, shipment, stock count).
- Every movement records the valuation rate applied at the time.

> This table is the audit backbone. If it and the journal entries survive, the state of the business can be reconstructed.

### 7.5 StockReservation

`sales_order_line, material, location, quantity_reserved, quantity_released, status, reserved_at, released_at`

- `status` ∈ `ACTIVE | PARTIALLY_RELEASED | RELEASED | CANCELLED`
- Reservation is a soft claim — it reduces `quantity_available` without moving stock. Distinct from allocation (which names specific rolls) and issue (which moves stock). See Architecture §6.3.

### 7.6 StockCount

`count_number, count_date, location, status, counted_by, approved_by`

**StockCountLine** — `stock_count, material, roll_or_lot_reference, system_quantity, counted_quantity, variance_quantity, variance_value, adjustment_movement (nullable)`

- Approving a count posts adjustment movements. The count lines retain the system quantity as it stood at count time.

---

## 8. Production

### 8.1 WorkOrder

`work_order_number, sales_order_line, style, colourway, quantity, planned_start_date, planned_end_date, actual_start_date, actual_end_date, line_reference, status, priority`

- `status` ∈ `PLANNED | RELEASED | IN_PROGRESS | COMPLETED | CLOSED | CANCELLED`
- **WorkOrderSizeBreakdown** — `work_order, style_size, quantity`

### 8.2 CutPlan

`cut_plan_number, work_order, marker_length, marker_width, marker_efficiency, ply_count, lay_count, planned_cut_quantity, actual_cut_quantity, fabric_consumed, status, cut_date`

**CutPlanSizeRatio** — `cut_plan, style_size, ratio_per_ply`

**CutPlanRollAllocation** — `cut_plan, fabric_roll, allocated_length, consumed_length, status`

- The allocation table is where roll identity meets production. It is what makes shade-lot consistency enforceable and traceability real.
- Consuming from a cut plan posts an issue movement against each allocated roll and decrements `remaining_length`.

### 8.3 ProductionStageProgress

`work_order, stage, style_size, quantity_input, quantity_output, quantity_rejected, quantity_rework, production_date, line_reference, recorded_by`

- `stage` ∈ `CUTTING | SEWING | FINISHING | PACKING`
- Append-only daily entries. WIP at any stage is derived by summing inputs and outputs, not stored.
- Unique on `(work_order, stage, style_size, production_date, line_reference)`.

### 8.4 Subcontracting

**SubcontractIssue** — `issue_number, work_order, subcontractor, issue_date, expected_return_date, status`
**SubcontractIssueLine** — `subcontract_issue, material, roll_or_lot_reference, quantity, uom, rate`

**SubcontractReceipt** — `receipt_number, subcontract_issue, receipt_date, status`
**SubcontractReceiptLine** — `subcontract_receipt, style_size, quantity_received, quantity_rejected, processing_charge`

- Material issued to a subcontractor remains company stock, held at a subcontractor-type `Location`. It never leaves the balance sheet.

---

## 9. Quality

### 9.1 DefectType

`code, name, stage (INBOUND_FABRIC | INBOUND_TRIM | INLINE | FINAL), severity (CRITICAL | MAJOR | MINOR), is_active`

Configurable master — factories and buyers use different defect catalogues.

### 9.2 InboundInspection

`inspection_number, grn_line, fabric_roll (nullable), material_lot (nullable), inspection_date, inspected_by, inspected_quantity, method, total_defect_points, points_per_100_sqyd, result, remarks`

- `method` ∈ `FOUR_POINT | TEN_POINT | VISUAL | AQL`
- `result` ∈ `ACCEPTED | REJECTED | ACCEPTED_WITH_DEVIATION`
- For the 4-point system, `points_per_100_sqyd` is calculated from total points, roll length, and width. The threshold for acceptance is configurable per customer or material.

**InboundInspectionDefect** — `inspection, defect_type, position_metres, points_awarded, remarks`

### 9.3 InlineInspection

`inspection_number, work_order, stage, line_reference, inspection_date, inspected_by, checkpoint, sample_size, defects_found, defect_rate, result, corrective_action`

**InlineInspectionDefect** — `inspection, defect_type, quantity`

### 9.4 FinalInspection

`inspection_number, work_order, shipment (nullable), inspection_date, inspected_by, lot_size, inspection_level, aql_limit, sample_size, critical_defects, major_defects, minor_defects, acceptance_number, rejection_number, result, remarks`

- `result` ∈ `PASS | FAIL | PASS_WITH_DEVIATION`
- The accept/reject decision is derived from the AQL table for the given lot size and inspection level, but the derived numbers are **stored on the record** — AQL tables can be reconfigured, and a past inspection must remain interpretable exactly as it was decided.

**FinalInspectionDefect** — `inspection, defect_type, quantity, severity`

### 9.5 ReworkRecord

`work_order, stage, defect_type, quantity_sent, quantity_recovered, quantity_scrapped, rework_date, labour_minutes, cost_impact, remarks`

---

## 10. Costing

### 10.1 CostSheet

`cost_sheet_number, style, customer, sales_order (nullable), version, currency, exchange_rate, status, effective_date, prepared_by, approved_by`

- `status` ∈ `DRAFT | APPROVED | SUPERSEDED`
- Versioned. Approving a new version supersedes the previous one; nothing is overwritten.

**CostSheetLine**
`cost_sheet, cost_head, material (nullable), description, quantity_per_garment, uom, rate, wastage_percentage, amount`

- `cost_head` ∈ `FABRIC | TRIM | ACCESSORY | PACKAGING | CMT | OVERHEAD | FREIGHT | COMMISSION | FINANCE_COST | OTHER`
- Sheet totals derive a cost per garment, a target margin, and a target FOB price.

### 10.2 ActualCostEntry

`sales_order, work_order (nullable), cost_head, source_type, source_reference, quantity, uom, rate, amount, entry_date`

- Populated automatically from stock issues (material), production progress and standard minutes (labour), subcontract receipts (processing), and rework records.
- `source_type` / `source_reference` maintain the link back to the originating transaction, so any actual cost figure can be drilled into.

### 10.3 CostVariance

A derived read model rather than a stored table at MVP — computed per `(sales_order, cost_head)` as estimated versus actual, with absolute and percentage variance. Materialised only if performance requires it.

---

## 11. Finance

### 11.1 ChartOfAccount

`account_code, account_name, account_type (ASSET | LIABILITY | EQUITY | INCOME | EXPENSE), parent_account, is_group, is_active`

Configurable hierarchy. Group accounts hold no postings.

### 11.2 SalesInvoice

`invoice_number, customer, sales_order, shipment, invoice_date, due_date, currency, exchange_rate, subtotal, tax_amount, total_amount, amount_paid, status`

- `status` ∈ `DRAFT | POSTED | PARTIALLY_PAID | PAID | CANCELLED`
- Posted invoices are immutable; cancellation posts a credit note rather than editing the invoice.

**SalesInvoiceLine** — `invoice, shipment_line, description, quantity, uom, rate, amount, tax_rate, tax_amount`

### 11.3 PurchaseInvoice

`invoice_number, supplier, purchase_order, grn, invoice_date, due_date, currency, exchange_rate, subtotal, tax_amount, total_amount, amount_paid, status, supplier_invoice_reference`

**PurchaseInvoiceLine** — `invoice, grn_line, description, quantity, uom, rate, amount, tax_rate, tax_amount`

- The three-way match (PO ↔ GRN ↔ invoice) compares quantity and rate across the linked records and flags variance beyond a configurable tolerance.

### 11.4 Payment

`payment_number, payment_type (RECEIPT | PAYMENT), party_type, party_id, payment_date, currency, exchange_rate, amount, payment_method, bank_reference, status`

**PaymentAllocation** — `payment, invoice_type, invoice_id, allocated_amount`

- One payment may settle several invoices; one invoice may receive several payments.

### 11.5 JournalEntry

`entry_number, entry_date, reference_type, reference_id, narration, total_debit, total_credit, status, posted_by, posted_at`

**JournalEntryLine** — `journal_entry, account, debit_amount, credit_amount, currency, exchange_rate, base_debit_amount, base_credit_amount, cost_centre (nullable)`

- Check constraint: `total_debit = total_credit` on every entry.
- **Append-only.** A reversal posts a new entry with `reverses_entry_id` set. See Architecture §6.4.
- Every stock movement with financial consequence and every invoice or payment generates a journal entry.

---

## 12. Key Relationships

The chains that matter most, expressed as traceability paths:

**Demand to supply**
`SalesOrder → SalesOrderLine → BillOfMaterial → PurchaseRequisition → PurchaseOrder → GRN → FabricRoll`

**Roll to garment (the traceability chain)**
`FabricRoll → CutPlanRollAllocation → CutPlan → WorkOrder → ProductionStageProgress → ShipmentLine → SalesInvoiceLine`

**Order to cost**
`SalesOrder → CostSheet (estimated)` and `SalesOrder → ActualCostEntry (actual)` → variance

**Any stock figure to its history**
`StockBalance → StockMovement (filtered by material, location, reference)`

**Any financial figure to its source**
`JournalEntryLine → JournalEntry → reference_type/reference_id → source document`

## 13. Integrity Rules

Enforced at the database level wherever possible:

| # | Rule |
|---|------|
| DR-01 | `FabricRoll.remaining_length` between 0 and `received_length` |
| DR-02 | `GRNLine.received_quantity = accepted_quantity + rejected_quantity` |
| DR-03 | `JournalEntry.total_debit = total_credit` |
| DR-04 | `StockBalance.quantity_reserved <= quantity_on_hand` |
| DR-05 | `SalesOrderLine.quantity = SUM(SizeBreakdown.quantity)` — validated on confirmation |
| DR-06 | Shipped quantity per order line cannot exceed ordered quantity |
| DR-07 | `StockMovement` and `JournalEntry` reject UPDATE and DELETE |
| DR-08 | A `Material` with `tracking_mode = ROLL` cannot receive stock without a `FabricRoll` record |
| DR-09 | Cut plan consumption cannot exceed allocated roll length |
| DR-10 | Payment allocations against an invoice cannot exceed the invoice total |
| DR-11 | Unique document numbers per series and financial year |
| DR-12 | A master record referenced by any transaction cannot be hard-deleted |

## 14. Open Data Questions

| # | Question | Blocks |
|---|----------|--------|
| D1 | Tax model — single rate, multi-component (CGST/SGST/IGST), or jurisdiction-based? | Invoice line structure |
| D2 | Is standard costing needed alongside weighted average? | Valuation design |
| D3 | Multi-plant at V1 — does `Company` need to become plant-scoped? | Location hierarchy |
| D4 | Legacy data to migrate, and in what shape? | Master data field set |
| D5 | Are cost centres required at MVP, or deferred? | Journal entry line structure |

---

## Revision History

| Version | Date | Author | Change |
|---------|------|--------|--------|
| v0.1 | 23 Jul 2026 | Rupesh | Initial draft |
