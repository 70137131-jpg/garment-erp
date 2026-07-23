# Business Rules Catalogue — Textile & Apparel ERP

**Document:** 05 — Business Rules
**Status:** Draft v0.1
**Owner:** Rupesh
**Last updated:** 23 July 2026
**Related:** `01-PRD-textile-erp.md`, `03-Data-Model.md`, `04-System-Architecture.md`

---

## 1. Purpose

Every calculation, validation, and state rule the system enforces, with worked numeric examples. This is the document an implementer checks their output against.

**Why the examples matter:** a wrong consumption formula produces plausible numbers. It compiles, it runs, nobody notices until fabric runs short at the cutting table. Each rule below carries a worked example precisely so implementation can be verified, not assumed.

## 2. Conventions

- **Rounding:** quantities to 4 decimal places, money to the currency's minor unit. Round half up.
- **Rounding timing:** round only at the point of storage, never mid-calculation. Intermediate values carry full precision.
- **Percentages:** stored as the number, not the fraction. 3% is stored as `3.0000`, applied as `× (1 + 3/100)`.
- **Rule IDs** are referenced from test cases and code comments.
- Every rule states its **failure mode** — what happens when validation fails.

---

## 3. Units of Measure

### BR-UOM-01 — Conversion within a category

Conversion is permitted only within a UOM category (LENGTH, WEIGHT, COUNT, AREA). Cross-category conversion requires a material-specific factor.

```
quantity_in_base = quantity_entered × conversion_factor
```

**Worked example**
```
Material:            Cotton poplin
Base UOM:            metres
Entered:             500 yards
Conversion factor:   0.9144 (yards → metres)

quantity_in_base = 500 × 0.9144 = 457.2000 metres
```

**Failure mode:** conversion between categories without a material factor raises `InvalidUOMConversion`. Never fall back to a factor of 1.

### BR-UOM-02 — Storage is always base UOM

Stock balances, valuations, and ledger entries store base UOM only. The entry UOM and factor are retained on the transaction record for audit.

**Worked example**
```
PO line entered:     500 yards @ 2.50 USD/yard
Stored quantity:     457.2000 metres
Stored rate:         2.7340 USD/metre   (2.50 ÷ 0.9144)
Retained on record:  entry_uom = yards, entry_qty = 500, factor = 0.9144
```

Line value is identical either way — 1,250.00 USD. Verify this in tests: converting the rate and converting the quantity must produce the same total.

---

## 4. Bill of Materials and Consumption

### BR-BOM-01 — Consumption per garment with wastage

```
gross_consumption = net_consumption × (1 + wastage_percentage / 100)
```

**Worked example**
```
Style:               MS-4471 men's shirt
Material:            Cotton poplin
Net consumption:     1.4500 metres/garment
Wastage:             5.00%

gross_consumption = 1.4500 × 1.05 = 1.5225 metres/garment
```

### BR-BOM-02 — Size-dependent consumption

Where `size_dependent = true`, consumption is taken per size from `BOMSizeConsumption`. Total requirement sums across the size breakdown.

**Worked example**
```
Order line: 1,200 pcs across sizes

Size   Qty    Net/garment   Wastage 5%   Gross/garment   Total
S      200    1.3000        ×1.05        1.3650          273.0000
M      400    1.4000        ×1.05        1.4700          588.0000
L      400    1.5000        ×1.05        1.5750          630.0000
XL     200    1.6000        ×1.05        1.6800          336.0000
                                                       ──────────
                                                        1,827.0000 metres
```

**Common error:** averaging consumption across sizes and multiplying by total quantity. Using the mid value (1.4500 × 1.05 × 1200 = 1,827.0000) coincidentally matches here because the size curve is symmetric. It will not match for a skewed curve — test with S=800, XL=100 to confirm the implementation sums per size rather than averaging.

### BR-BOM-03 — Colour-specific BOM lines

A BOM line with a `colourway` applies only to that colourway. A line with null colourway applies to all.

**Failure mode:** two BOM lines for the same material where one specifies a colourway and one does not, and both would apply — raises `AmbiguousBOMLine` at BOM approval, not at explosion time.

---

## 5. Material Requirement Planning

### BR-MRP-01 — Gross requirement on order confirmation

```
gross_requirement = Σ (order_line_qty × gross_consumption_per_garment)
```
summed across all order lines and BOM lines for the material.

### BR-MRP-02 — Net requirement after stock and reservations

```
available_stock  = quantity_on_hand − quantity_reserved
incoming         = Σ open PO quantities arriving before required_by_date
net_requirement  = max(0, gross_requirement − available_stock − incoming)
```

**Worked example**
```
Material:              Cotton poplin, navy
Gross requirement:     1,827.0000 metres
On hand:                 900.0000 metres
Reserved (other orders): 300.0000 metres
Available:               600.0000 metres
Open PO arriving in time: 400.0000 metres

net_requirement = max(0, 1827.0000 − 600.0000 − 400.0000)
                = 827.0000 metres  → shortage flagged
```

**Failure mode:** a positive net requirement does not block confirmation. It raises a shortage flag on the order and generates a requisition suggestion. Blocking would prevent legitimate make-to-order confirmation.

### BR-MRP-03 — Incoming stock counts only if it arrives in time

A PO line counts toward `incoming` only where `expected_delivery_date <= required_by_date`. A late PO is not supply.

---

## 6. Inventory

### BR-INV-01 — Stock balance is derived, never independently set

```
quantity_on_hand  = Σ (inbound movements) − Σ (outbound movements)
quantity_available = quantity_on_hand − quantity_reserved
```

`StockBalance` is materialised inside the same transaction as the movement. It is never written by any other path.

**Failure mode:** if a recalculation from `StockMovement` disagrees with `StockBalance`, the balance is wrong and the ledger is right. A nightly reconciliation job compares them and alerts on variance. This check should be built early — it is the canary for the entire inventory module.

### BR-INV-02 — Roll issue decrements remaining length

```
new_remaining = current_remaining − issued_length
```

**Constraint:** `issued_length <= current_remaining`. Never negative.

**Worked example**
```
Roll R-00841
Received length:   120.0000 m
Remaining before:   85.5000 m
Issue to cut plan:  42.3000 m

new_remaining = 85.5000 − 42.3000 = 43.2000 m
Roll status:    PARTIALLY_ISSUED
```

When `new_remaining` reaches zero, status becomes `ISSUED`. When it falls below the configured remnant threshold (default 3.0000 m) but is above zero, the roll is flagged as a remnant — still available, but excluded from primary allocation suggestions.

**Failure mode:** issuing more than remaining raises `InsufficientRollLength`. No partial fulfilment; the caller must split the issue across rolls explicitly.

### BR-INV-03 — Weighted average cost on receipt

```
new_avg_cost = (existing_qty × existing_avg_cost + received_qty × receipt_rate)
               ÷ (existing_qty + received_qty)
```

**Worked example**
```
Existing:   600.0000 m @ 2.7340 USD/m  → value 1,640.40
Received:   457.2000 m @ 2.9500 USD/m  → value 1,348.74

new_avg_cost = (1,640.40 + 1,348.74) ÷ (600.0000 + 457.2000)
             = 2,989.14 ÷ 1,057.2000
             = 2.8274 USD/m
```

**Edge case:** where `existing_qty` is zero, `new_avg_cost = receipt_rate`. Where existing quantity is negative (which should be impossible — see BR-INV-01), raise `NegativeStockValuation` rather than computing.

### BR-INV-04 — Issue valuation

Issues are valued at the weighted average cost at the moment of issue. The average does not change on issue — only on receipt.

**Worked example**
```
Issue:      42.3000 m @ 2.8274 USD/m
Issue value: 119.60 USD  (42.3000 × 2.8274 = 119.5990, rounded to 2dp)
```

### BR-INV-05 — Reservation cannot exceed available

```
Constraint: quantity_reserved <= quantity_on_hand
```

Reservation reduces `quantity_available` without creating a movement. Releasing a reservation restores it.

**Failure mode:** raises `ReservationConflict` when the reservation would exceed available stock. The row lock (Architecture §6.3) is what makes this check reliable under concurrency — without it, two simultaneous reservations both read the same available figure and both succeed.

### BR-INV-06 — Negative stock is never permitted

No configuration option enables it. An issue exceeding available stock fails.

**Rationale:** negative stock in a factory means someone has already cut fabric that the system says does not exist. Permitting it hides the error rather than surfacing it.

---

## 7. Production

### BR-PRD-01 — Fabric requirement for a cut plan

```
fabric_required = (marker_length + end_allowance) × ply_count × lay_count
```

`end_allowance` covers the leading and trailing waste per lay — factory-configurable, default 0.1000 m per lay.

**Worked example**
```
Marker length:     6.2000 m
End allowance:     0.1000 m
Ply count:         48
Lay count:         5

fabric_required = (6.2000 + 0.1000) × 48 × 5 = 1,512.0000 m
```

### BR-PRD-02 — Expected cut quantity

```
garments_per_ply     = Σ (size ratio in marker)
expected_cut_quantity = garments_per_ply × ply_count × lay_count
```

**Worked example**
```
Marker ratio:   S:1, M:2, L:2, XL:1  → 6 garments per ply
Ply count:      48
Lay count:      5

expected_cut_quantity = 6 × 48 × 5 = 1,440 pcs

Per size:
  S  = 1 × 48 × 5 = 240
  M  = 2 × 48 × 5 = 480
  L  = 2 × 48 × 5 = 480
  XL = 1 × 48 × 5 = 240
                    ─────
                    1,440
```

**Validation:** per-size cut quantities must not exceed order size quantities by more than the configured cutting tolerance (default 3%). Exceeding raises a warning, not a block — overcutting is sometimes deliberate.

### BR-PRD-03 — Marker efficiency

```
marker_efficiency = (total_pattern_area ÷ (marker_length × marker_width)) × 100
```

**Worked example**
```
Total pattern area:  10.4100 m²
Marker length:        6.2000 m
Marker width:         1.4200 m
Marker area:          8.8040 m²

marker_efficiency = (10.4100 ÷ 8.8040) × 100 = 118.24%
```

That result is impossible — pattern area cannot exceed marker area. It indicates transposed or wrongly-scaled inputs. **The system must reject efficiency above 100%** with `InvalidMarkerEfficiency` rather than storing it.

Corrected:
```
Total pattern area:   7.4800 m²
Marker area:          8.8040 m²
marker_efficiency = (7.4800 ÷ 8.8040) × 100 = 84.96%
```

Typical range is 75–90%. Below 60% or above 95% warrants a warning.

### BR-PRD-04 — Actual fabric consumption per garment

```
actual_consumption_per_garment = fabric_consumed ÷ actual_cut_quantity
```

**Worked example**
```
Fabric consumed:      1,498.5000 m  (from roll issues)
Actual cut quantity:  1,432 pcs

actual = 1,498.5000 ÷ 1,432 = 1.0464 m/garment
```

Compared against BOM gross consumption. Variance beyond a configured threshold (default 5%) flags the cut plan for review.

### BR-PRD-05 — WIP at a stage

```
wip_at_stage = Σ (quantity_input) − Σ (quantity_output) − Σ (quantity_rejected)
```

Derived from `ProductionStageProgress`, never stored.

**Worked example**
```
Sewing stage, work order WO-00219
Input recorded:      1,432
Output recorded:     1,180
Rejected:               24

wip_at_sewing = 1,432 − 1,180 − 24 = 228 pcs
```

**Constraint:** WIP cannot be negative. A negative result means output was recorded before input — raises `InvalidStageProgress`.

### BR-PRD-06 — Stage sequence

Output from one stage becomes available input to the next. Cumulative input at stage N cannot exceed cumulative output at stage N−1.

```
Cutting output:    1,432
Sewing input:      1,432   ✓ valid
Sewing input:      1,500   ✗ raises InvalidStageProgress
```

---

## 8. Quality

### BR-QC-01 — Four-point fabric inspection scoring

Defect points are awarded by defect length, per the 4-point system:

| Defect length | Points |
|---------------|--------|
| Up to 3 inches (7.62 cm) | 1 |
| Over 3 to 6 inches | 2 |
| Over 6 to 9 inches | 3 |
| Over 9 inches | 4 |

**Maximum 4 points per linear yard.** Holes larger than 1 inch score 4 points regardless of length.

### BR-QC-02 — Points per 100 square yards

```
points_per_100_sqyd = (total_points × 3600) ÷ (roll_length_yards × roll_width_inches)
```

The constant 3600 converts to 100 square yards: 100 sq yd = 3600 sq inches per yard of length.

**Worked example**
```
Roll R-00841
Total defect points:   28
Roll length:          131.2336 yards  (120.0000 m)
Roll width:            56 inches

points_per_100_sqyd = (28 × 3600) ÷ (131.2336 × 56)
                    = 100,800 ÷ 7,349.08
                    = 13.72
```

**Acceptance:** threshold is configurable per customer or material, commonly 20 or 25 points. At threshold 20, this roll passes.

**Failure mode:** a roll exceeding threshold is set to `QUARANTINE`, not `REJECTED`. Rejection is a commercial decision requiring authorisation, not an automatic consequence.

### BR-QC-03 — AQL sample size determination

Sample size is looked up from the lot size and inspection level (ISO 2859-1 / ANSI Z1.4). Standard is General Inspection Level II.

| Lot size | Code letter | Sample size |
|----------|-------------|-------------|
| 151–280 | G | 32 |
| 281–500 | H | 50 |
| 501–1,200 | J | 80 |
| 1,201–3,200 | K | 125 |
| 3,201–10,000 | L | 200 |

### BR-QC-04 — AQL accept and reject numbers

At AQL 2.5 for major defects:

| Sample size | Accept | Reject |
|-------------|--------|--------|
| 32 | 2 | 3 |
| 50 | 3 | 4 |
| 80 | 5 | 6 |
| 125 | 7 | 8 |
| 200 | 10 | 11 |

**Worked example**
```
Lot size:            1,180 pcs
Inspection level:    General II
Code letter:         J
Sample size:         80
AQL (major):         2.5
Accept number:       5
Reject number:       6

Inspected 80 pcs → 4 major defects found
4 <= 5  →  result: PASS
```

**Critical rule:** the accept number, reject number, and sample size are **stored on the inspection record**, not looked up when the record is read. AQL tables are configurable; a past inspection must remain interpretable exactly as it was decided.

**Critical defects:** any critical defect fails the lot regardless of the accept number. This is not a count-based decision.

### BR-QC-05 — Defect rate

```
defect_rate = (defects_found ÷ sample_size) × 100
```

**Worked example**
```
defect_rate = (4 ÷ 80) × 100 = 5.00%
```

Recorded for trend reporting. It does not drive the accept/reject decision — that comes from BR-QC-04.

---

## 9. Costing

### BR-COST-01 — Cost sheet material line

```
line_amount = consumption_per_garment × (1 + wastage% / 100) × rate
```

**Worked example**
```
Cotton poplin:  1.4500 m/garment, 5% wastage, 2.9500 USD/m
line_amount = 1.4500 × 1.05 × 2.9500 = 4.4914 USD/garment
```

### BR-COST-02 — Cost sheet totals

```
material_cost  = Σ (FABRIC + TRIM + ACCESSORY + PACKAGING lines)
conversion_cost = Σ (CMT + OVERHEAD lines)
total_cost     = material_cost + conversion_cost + Σ (FREIGHT + COMMISSION + FINANCE_COST + OTHER)
```

**Worked example — style MS-4471, per garment**
```
Fabric                    4.4914
Trims                     0.8200
Packaging                 0.3100
                        ────────
Material cost             5.6214

CMT                       2.1000
Overhead                  0.6300
                        ────────
Conversion cost           2.7300

Freight                   0.1800
Commission                0.2500
                        ────────
Total cost                8.7814 USD/garment
```

### BR-COST-03 — Target FOB from margin

```
target_fob = total_cost ÷ (1 − margin% / 100)
```

**Worked example**
```
Total cost:  8.7814 USD
Margin:      18.00%

target_fob = 8.7814 ÷ (1 − 0.18) = 8.7814 ÷ 0.82 = 10.7090 USD
```

**Common error:** `total_cost × 1.18 = 10.3620`. That is a 15.25% margin on selling price, not 18%. **Markup on cost and margin on price are different calculations.** The system uses margin on price. Test both figures explicitly.

### BR-COST-04 — Actual material cost

```
actual_material_cost = Σ (issued_quantity × valuation_rate at issue)
```

Sourced from `StockMovement` records linked to the order's work orders.

**Worked example**
```
Cotton poplin  1,498.5000 m @ 2.8274 = 4,236.87
Interlining       88.2000 m @ 1.1500 =   101.43
Thread            42 cones  @ 2.4000 =   100.80
Buttons        8,592 pcs    @ 0.0300 =   257.76
                                      ──────────
                                        4,696.86 USD
```

### BR-COST-05 — Actual labour cost

```
labour_cost = Σ (standard_minutes × output_quantity) ÷ 60 × hourly_rate
```

Uses standard minutes from the style's operation list applied to recorded output.

**Worked example**
```
Total standard minutes/garment:  22.5000
Output:                          1,156 pcs
Hourly rate:                     3.2000 USD

labour_cost = (22.5000 × 1,156) ÷ 60 × 3.2000
            = 26,010 ÷ 60 × 3.2000
            = 433.50 × 3.2000
            = 1,387.20 USD
```

### BR-COST-06 — Cost variance

```
variance_amount     = actual_cost − estimated_cost
variance_percentage = (variance_amount ÷ estimated_cost) × 100
```

Positive variance means overspend.

**Worked example — order SO-00318, 1,156 pcs shipped**
```
Cost head    Estimated    Actual      Variance    Variance %
Material     6,498.34     4,696.86    −1,801.48     −27.72%
Labour       1,213.80     1,387.20      +173.40     +14.29%
Overhead       728.28       728.28         0.00       0.00%
Freight        208.08       241.50       +33.42     +16.06%
            ─────────    ─────────    ─────────
Total        8,648.50     7,053.84    −1,594.66     −18.44%
```

Note the material variance is large and negative — worth investigating rather than celebrating. It usually indicates the cost sheet used an outdated fabric rate, or that consumption was estimated against ordered quantity while actuals reflect cut quantity. Estimated cost must be scaled to the same quantity basis as actual before comparison.

### BR-COST-07 — Estimated cost scaling

```
scaled_estimate = cost_sheet_per_garment × actual_shipped_quantity
```

Comparing a cost sheet built for 1,200 pcs against actuals for 1,156 pcs without scaling produces a spurious favourable variance.

---

## 10. Finance

### BR-FIN-01 — Invoice line amount

```
line_amount = quantity × rate
tax_amount  = line_amount × tax_rate / 100
line_total  = line_amount + tax_amount
```

### BR-FIN-02 — Invoice totals

```
subtotal     = Σ line_amount
tax_total    = Σ tax_amount
total_amount = subtotal + tax_total
```

**Worked example**
```
Line 1:  1,156 pcs @ 10.7090 = 12,379.60
Tax @ 0% (export)            =      0.00
                              ──────────
Total                          12,379.60 USD
```

*Tax treatment beyond a single rate depends on PRD open question D1.*

### BR-FIN-03 — Multi-currency conversion

```
base_amount = transaction_amount × exchange_rate_at_transaction_date
```

The rate is captured on the record. Historical documents never revalue.

**Worked example**
```
Invoice:        12,379.60 USD
Rate on date:   0.9180 EUR/USD (base currency EUR)
base_amount = 12,379.60 × 0.9180 = 11,364.47 EUR
```

### BR-FIN-04 — Journal entry balance

```
Constraint: Σ debit_amount = Σ credit_amount   (per entry)
```

Enforced as a database check constraint. Never as application-only validation.

**Worked example — goods receipt**
```
Account                    Debit      Credit
Inventory — fabric       1,348.74
GRN clearing                         1,348.74
                        ─────────   ─────────
                         1,348.74    1,348.74  ✓
```

**Worked example — sales invoice**
```
Account                    Debit      Credit
Accounts receivable     12,379.60
Sales revenue                       12,379.60

Cost of goods sold       7,053.84
Inventory — finished                 7,053.84
                        ─────────   ─────────
                        19,433.44   19,433.44  ✓
```

### BR-FIN-05 — Three-way match tolerance

```
quantity_variance% = ((invoice_qty − grn_qty) ÷ grn_qty) × 100
rate_variance%     = ((invoice_rate − po_rate) ÷ po_rate) × 100
```

Default tolerance 2% on quantity, 1% on rate. Both configurable.

**Worked example**
```
PO rate:        2.9500 USD/m
GRN quantity:   457.2000 m
Invoice:        457.2000 m @ 2.9800 USD/m

quantity_variance = 0.00%   → within tolerance
rate_variance = ((2.9800 − 2.9500) ÷ 2.9500) × 100 = 1.02%  → exceeds 1%

Result: flagged for approval, invoice not blocked
```

**Failure mode:** variance flags for approval. It does not block posting — a purchase invoice may legitimately differ, and blocking creates a worse problem than flagging.

### BR-FIN-06 — Payment allocation

```
Constraint: Σ allocated_amount <= payment.amount
Constraint: Σ allocations against an invoice <= invoice.total_amount
```

**Worked example**
```
Payment received:  20,000.00 USD

Allocated:
  INV-00291   12,379.60
  INV-00304    6,140.00
                ─────────
                18,519.60
Unallocated:     1,480.40  → held as advance
```

### BR-FIN-07 — Ageing buckets

Computed from `due_date` against the report date:

```
Current:    due_date >= report_date
1–30:       1 to 30 days overdue
31–60:      31 to 60 days overdue
61–90:      61 to 90 days overdue
90+:        over 90 days overdue
```

Ageing uses outstanding amount (`total_amount − amount_paid`), not invoice total.

---

## 11. Document Numbering

### BR-NUM-01 — Number format

```
number = prefix + financial_year + separator + zero_padded_sequence
```

**Worked example**
```
Prefix:      SO
Year:        2627   (FY 2026–27)
Separator:   -
Padding:     5
Sequence:    318

number = "SO2627-00318"
```

### BR-NUM-02 — Gapless allocation

Sequence allocation takes a row lock on `NumberSeries` for the duration. The number is consumed on document creation, not on save.

**Failure mode:** if a transaction rolls back after allocating a number, the number is consumed and a gap results. This is deliberate — reusing numbers is worse than a gap. Document the gap; never fill it.

---

## 12. State Transition Rules

### BR-STATE-01 — Sales order

| From | To | Condition | Side effect |
|------|-----|-----------|-------------|
| DRAFT | CONFIRMED | Lines exist, quantities match breakdowns | Number allocated, MRP run, reservations created |
| CONFIRMED | IN_PRODUCTION | At least one work order released | — |
| IN_PRODUCTION | PARTIALLY_SHIPPED | First shipment posted | Reservations partially released |
| PARTIALLY_SHIPPED | SHIPPED | Cumulative shipped = ordered | Remaining reservations released |
| SHIPPED | CLOSED | Fully invoiced and paid | — |
| Any pre-shipment | CANCELLED | No shipment exists | All reservations released |

Transitions not listed are invalid and raise `InvalidStateTransition`.

### BR-STATE-02 — Purchase order

| From | To | Condition |
|------|-----|-----------|
| DRAFT | SENT | Supplier and lines present |
| SENT | PARTIALLY_RECEIVED | GRN posted, received < ordered |
| SENT / PARTIALLY_RECEIVED | RECEIVED | Cumulative received >= ordered |
| RECEIVED | CLOSED | Invoice matched |
| DRAFT / SENT | CANCELLED | No GRN exists |

### BR-STATE-03 — Fabric roll

| From | To | Trigger |
|------|-----|---------|
| — | AVAILABLE | GRN accepted, inspection passed |
| — | QUARANTINE | Inspection exceeds defect threshold |
| QUARANTINE | AVAILABLE | Authorised deviation approval |
| QUARANTINE | REJECTED | Authorised rejection |
| AVAILABLE | RESERVED | Allocated to a cut plan |
| RESERVED | PARTIALLY_ISSUED | Partial length issued |
| PARTIALLY_ISSUED | ISSUED | Remaining length reaches zero |
| Any | SCRAPPED | Authorised scrap posting |

### BR-STATE-04 — Immutability after posting

Once posted, these records cannot be edited: `StockMovement`, `JournalEntry`, `SalesInvoice` (POSTED), `PurchaseInvoice` (POSTED), `GoodsReceiptNote` (accepted), any completed inspection.

Corrections post reversing entries. Attempting an update raises `ImmutableRecord`.

---

## 13. Validation Rules Summary

| ID | Rule | Failure |
|----|------|---------|
| VR-01 | Order line quantity = sum of size breakdown | `QuantityMismatch` at confirmation |
| VR-02 | Roll remaining length between 0 and received | Database check constraint |
| VR-03 | GRN received = accepted + rejected | Database check constraint |
| VR-04 | Journal entry debits = credits | Database check constraint |
| VR-05 | Reserved <= on hand | `ReservationConflict` |
| VR-06 | Shipped <= ordered per line | `OvershipmentNotPermitted` |
| VR-07 | Cut consumption <= allocated roll length | `InsufficientRollLength` |
| VR-08 | Payment allocation <= invoice outstanding | `OverAllocation` |
| VR-09 | Marker efficiency <= 100% | `InvalidMarkerEfficiency` |
| VR-10 | Stage input <= prior stage output | `InvalidStageProgress` |
| VR-11 | WIP >= 0 at every stage | `InvalidStageProgress` |
| VR-12 | Document numbers unique per series and year | Database unique constraint |

---

## 14. Configurable Parameters

Defaults, all editable by an administrator without deployment:

| Parameter | Default |
|-----------|---------|
| Cut end allowance per lay | 0.1000 m |
| Cutting tolerance | 3.00% |
| Remnant threshold | 3.0000 m |
| Consumption variance alert | 5.00% |
| Four-point acceptance threshold | 20 points/100 sq yd |
| AQL major defect level | 2.5 |
| AQL inspection level | General II |
| Three-way match quantity tolerance | 2.00% |
| Three-way match rate tolerance | 1.00% |
| Default wastage | 5.00% |
| Valuation method | Weighted average |

---

## 15. Open Rule Questions

| # | Question | Blocks |
|---|----------|--------|
| R1 | Tax model — single rate or multi-component? | BR-FIN-01, BR-FIN-02 |
| R2 | Is FIFO valuation needed alongside weighted average? | BR-INV-03 |
| R3 | Are AQL levels other than General II required? | BR-QC-03 |
| R4 | Labour costing — standard minutes or actual hours booked? | BR-COST-05 |
| R5 | Overhead allocation basis — per garment, per minute, or per order? | BR-COST-02 |

---

## Revision History

| Version | Date | Author | Change |
|---------|------|--------|--------|
| v0.1 | 23 Jul 2026 | Rupesh | Initial draft |
