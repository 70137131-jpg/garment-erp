"""6.8 — actual costing, variance decomposition, and GL reclassification.

The load-bearing assertion in this module is the *reconciliation identity*:

    actual_total_cost − std_total_cost  ==  Σ variance amounts

A variance report that does not reconstruct the total is actively harmful — it
looks authoritative while quietly losing money between the lines. Every test
that builds a run re-checks it.
"""

from decimal import Decimal

from app.finance.service import account_balance, account_by_code
from app.inventory.models import MovementType
from app.inventory.service import post_movement


def _scenario(c, *, issue_all=True, produced=299):
    """Drive masters → receipt → cut → sew far enough to have real actuals.

    Mirrors the Part C spine but keeps the cost sheet linked to the *material*,
    which is what lets the engine compare issued quantity against standard.
    """
    sr = c.post("/masters/size-ranges", json={
        "code": "MENS-STD", "name": "Mens Standard",
        "sizes": [{"position": 1, "label": "S"}, {"position": 2, "label": "M"},
                  {"position": 3, "label": "L"}, {"position": 4, "label": "XL"}],
    }).json()["id"]
    navy = c.post("/masters/colours", json={"code": "NAVY", "name": "Navy"}).json()["id"]
    customer = c.post("/masters/customers", json={"name": "ACME Apparel"}).json()["id"]
    supplier = c.post("/masters/suppliers", json={
        "name": "Textile Mills", "material_types": ["fabric"]}).json()["id"]
    fabric = c.post("/masters/materials", json={
        "name": "Single Jersey 180gsm", "material_type": "fabric", "base_uom": "metre",
        "purchase_uom": "roll", "purchase_to_base_factor": "50", "lot_tracked": True,
        "width_cm": "150",
    }).json()["id"]

    style_id = c.post("/styles", json={
        "style_number": "TS-100", "description": "Crew Neck Tee", "size_range_id": sr,
        "gender": "mens", "standard_sam": "12.5",
    }).json()["id"]

    bom = c.post(f"/styles/{style_id}/bom-versions", json={"lines": [{
        "material_id": fabric, "wastage_pct": "0.05",
        "size_consumption": [{"size_label": "S", "consumption": "1.0"},
                             {"size_label": "M", "consumption": "1.2"},
                             {"size_label": "L", "consumption": "1.4"},
                             {"size_label": "XL", "consumption": "1.6"}],
    }]}).json()
    c.post(f"/styles/bom-versions/{bom['id']}/approve")

    # Cost sheet standard: 1.2 m + 5% wastage at 4.00/m, SAM 12.5 at 0.08/min
    # and 80% efficiency, 15% overhead.
    sheet = c.post(f"/costing/styles/{style_id}/cost-sheets", json={
        "base_size": "M", "sam": "12.5", "sewing_cost_per_min": "0.08",
        "sewing_efficiency_pct": "80", "overhead_pct": "0.15", "margin_pct": "0.20",
        "lines": [{"category": "material", "material_id": fabric,
                   "quantity": "1.2", "rate": "4.00", "wastage_pct": "0.05"}],
    }).json()
    c.post(f"/costing/cost-sheets/{sheet['id']}/approve")

    order = c.post("/sales-orders", json={
        "customer_id": customer, "customer_po_number": "PO-778",
        "lines": [{"style_id": style_id, "colour_id": navy, "unit_price": "10.00",
                   "sizes": [{"size_label": "S", "ordered_qty": 50},
                             {"size_label": "M", "ordered_qty": 100},
                             {"size_label": "L", "ordered_qty": 100},
                             {"size_label": "XL", "ordered_qty": 50}]}],
    }).json()
    c.post(f"/sales-orders/{order['id']}/confirm")

    po = c.post("/procurement/purchase-orders", json={
        "supplier_id": supplier, "sales_order_id": order["id"],
        "lines": [{"material_id": fabric, "ordered_qty": "1000", "unit_price": "4.00"}],
    }).json()
    gr = c.post("/procurement/goods-receipts", json={
        "purchase_order_id": po["id"], "client_key": "gr-001",
        "rolls": [{"purchase_order_line_id": po["lines"][0]["id"], "length": "500",
                   "width_cm": "150", "dye_lot": "DL-1", "shade_group": "SG-NAVY", "grade": "A"},
                  {"purchase_order_line_id": po["lines"][0]["id"], "length": "500",
                   "width_cm": "150", "dye_lot": "DL-1", "shade_group": "SG-NAVY", "grade": "A"}],
    }).json()
    for roll in gr["rolls"]:
        c.post("/quality/four-point-inspections",
               json={"roll_id": roll["roll_id"], "defects": [{"penalty_points": 2}]})

    cut = c.post("/production/cut-orders", json={
        "style_id": style_id, "colour_id": navy, "sales_order_id": order["id"],
        "sizes": [{"size_label": "S", "planned_qty": 50}, {"size_label": "M", "planned_qty": 100},
                  {"size_label": "L", "planned_qty": 100}, {"size_label": "XL", "planned_qty": 50}],
    }).json()
    c.post(f"/production/cut-orders/{cut['id']}/reserve-fabric", json={"shade_group": "SG-NAVY"})
    if issue_all:
        c.post(f"/production/cut-orders/{cut['id']}/issue-fabric", json={})
        c.post(f"/production/cut-orders/{cut['id']}/complete", json={"cut_qty": [
            {"size_label": "S", "planned_qty": 50}, {"size_label": "M", "planned_qty": 100},
            {"size_label": "L", "planned_qty": 99}, {"size_label": "XL", "planned_qty": 50}]})

    sew = c.post("/production/sewing-orders", json={
        "style_id": style_id, "line": "Line 1", "planned_qty": produced,
        "cut_order_id": cut["id"]}).json()
    c.post(f"/production/sewing-orders/{sew['id']}/daily-output", json={
        "output_date": "2026-07-20", "produced_qty": produced,
        "operators": 25, "working_minutes": 480})

    return {
        "order_id": order["id"], "style_id": style_id, "fabric": fabric,
        "cut_id": cut["id"], "sewing_id": sew["id"], "supplier": supplier,
        "sheet_id": sheet["id"],
    }


def _assert_reconciles(run):
    """actual − standard must equal the sum of the decomposed variances."""
    total = Decimal(run["actual_total_cost"]) - Decimal(run["std_total_cost"])
    parts = sum(Decimal(v["amount"]) for v in run["variances"])
    assert total == parts, f"variances {parts} do not reconstruct total {total}"
    assert Decimal(run["total_variance"]) == total


def test_actual_cost_run_decomposes_the_gap(client):
    ctx = _scenario(client)

    r = client.post(f"/costing/sales-orders/{ctx['order_id']}/actual-cost", json={})
    assert r.status_code == 201, r.text
    run = r.json()

    assert run["produced_qty"] == 299
    assert run["status"] == "draft"

    # ---- material -------------------------------------------------------- #
    # Issued 409.5 m at 4.00 (the ledger's FIFO valuation) = 1638.00
    assert Decimal(run["actual_material_qty"]) == Decimal("409.5000")
    assert Decimal(run["actual_material_cost"]) == Decimal("1638.00")
    # Standard for 299 garments: 1.2 × 1.05 = 1.26 m each → 376.74 m at 4.00
    assert Decimal(run["std_material_qty"]) == Decimal("376.7400")
    assert Decimal(run["std_material_cost"]) == Decimal("1506.96")

    variances = {v["variance_type"]: v for v in run["variances"]}
    # Bought and consumed at exactly the standard rate → no price variance.
    assert Decimal(variances["material_price"]["amount"]) == Decimal("0.00")
    # Over-consumption: (409.5 − 376.74) × 4.00 = 131.04, adverse.
    assert Decimal(variances["material_usage"]["amount"]) == Decimal("131.04")
    assert variances["material_usage"]["favourable"] is False

    # ---- labour ---------------------------------------------------------- #
    # Standard minutes allowed = 12.5 / 0.80 × 299 = 4671.875
    assert Decimal(run["std_minutes"]) == Decimal("4671.8750")
    # Actual = 25 operators × 480 minutes
    assert Decimal(run["actual_minutes"]) == Decimal("12000.0000")
    assert Decimal(run["std_labour_cost"]) == Decimal("373.75")
    assert Decimal(run["actual_labour_cost"]) == Decimal("960.00")
    # No wage evidence → rate held at standard, whole gap is efficiency.
    assert Decimal(variances["labour_rate"]["amount"]) == Decimal("0.00")
    assert Decimal(variances["labour_efficiency"]["amount"]) == Decimal("586.25")

    _assert_reconciles(run)
    assert Decimal(run["total_variance"]) == Decimal("717.29")


def test_actual_rate_override_creates_a_rate_variance(client):
    ctx = _scenario(client)
    run = client.post(
        f"/costing/sales-orders/{ctx['order_id']}/actual-cost",
        json={"actual_rate_per_min": "0.10"},
    ).json()

    variances = {v["variance_type"]: v for v in run["variances"]}
    # (0.10 − 0.08) × 12000 = 240.00 adverse.
    assert Decimal(variances["labour_rate"]["amount"]) == Decimal("240.00")
    assert Decimal(run["actual_labour_cost"]) == Decimal("1200.00")
    _assert_reconciles(run)


def test_overhead_override_and_favourable_variance(client):
    ctx = _scenario(client)
    run = client.post(
        f"/costing/sales-orders/{ctx['order_id']}/actual-cost",
        json={"overhead_absorbed": "100.00"},
    ).json()

    variances = {v["variance_type"]: v for v in run["variances"]}
    # Standard overhead 0.94 × 299 = 281.06; absorbed only 100 → favourable.
    assert Decimal(run["std_overhead_cost"]) == Decimal("281.06")
    assert Decimal(variances["overhead"]["amount"]) == Decimal("-181.06")
    assert variances["overhead"]["favourable"] is True
    _assert_reconciles(run)


def test_unplanned_material_lands_in_usage_variance(client, session):
    """Material issued that no cost sheet anticipated is still explained.

    Posted through the service because no HTTP route issues stock against an
    arbitrary reference — only production does, and it only knows about the BOM
    fabric. This is exactly the blind spot the usage variance has to cover.
    """
    ctx = _scenario(client)
    extra = client.post("/masters/materials", json={
        "name": "Interlining", "material_type": "trims", "base_uom": "metre",
        "purchase_uom": "metre", "purchase_to_base_factor": "1",
    }).json()["id"]
    post_movement(
        session, material_id=extra, movement_type=MovementType.receipt,
        quantity=Decimal("100"), unit_cost=Decimal("2.00"),
        reference_type="opening", actor="test",
    )
    post_movement(
        session, material_id=extra, movement_type=MovementType.issue,
        quantity=Decimal("25"), reference_type="cut_order",
        reference_id=ctx["cut_id"], actor="test",
    )
    session.commit()

    run = client.post(
        f"/costing/sales-orders/{ctx['order_id']}/actual-cost", json={}
    ).json()
    variances = {v["variance_type"]: v for v in run["variances"]}
    # 25 × 2.00 = 50.00 of wholly unplanned consumption on top of the 131.04.
    assert Decimal(variances["material_usage"]["amount"]) == Decimal("181.04")
    assert "without a cost-sheet standard" in (
        variances["material_usage"]["explanation"] or ""
    )
    _assert_reconciles(run)


def test_run_requires_production(client):
    """An order with nothing produced has no actual to cost."""
    ctx = _scenario(client, issue_all=False, produced=0)
    r = client.post(f"/costing/sales-orders/{ctx['order_id']}/actual-cost", json={})
    assert r.status_code == 422
    assert "produced" in r.json()["detail"].lower()


def test_posting_reclassifies_variance_without_changing_profit(finance_client, session):
    """The GL is actual-cost driven, so posting must move money, not add it."""
    c = finance_client
    ctx = _scenario(c)
    c.post("/quality/inline-inspections", json={
        "sewing_order_id": ctx["sewing_id"], "units_checked": 100, "defects_found": 3})
    c.post("/quality/final-inspections", json={
        "sales_order_id": ctx["order_id"], "lot_size": 300, "aql": "2.5", "defects_found": 2})
    c.post(f"/sales-orders/{ctx['order_id']}/ship")

    before = c.get("/finance/profit-and-loss").json()
    expense_before = Decimal(before["total_expense"])

    run = c.post(f"/costing/sales-orders/{ctx['order_id']}/actual-cost", json={}).json()
    posted = c.post(f"/costing/actual-cost/{run['id']}/post").json()

    assert posted["status"] == "posted"
    assert posted["journal_entry_id"] is not None
    assert posted["posted_by"]

    after = c.get("/finance/profit-and-loss").json()
    # Reclassification only: total cost of sales is untouched.
    assert Decimal(after["total_expense"]) == expense_before

    # …but the variance is now visible in its own accounts.
    usage = account_by_code(session, "6120")
    efficiency = account_by_code(session, "6140")
    assert account_balance(session, usage) == Decimal("131.04")
    assert account_balance(session, efficiency) == Decimal("586.25")


def test_a_run_posts_only_once(finance_client):
    c = finance_client
    ctx = _scenario(c)
    run = c.post(f"/costing/sales-orders/{ctx['order_id']}/actual-cost", json={}).json()
    assert c.post(f"/costing/actual-cost/{run['id']}/post").status_code == 200
    again = c.post(f"/costing/actual-cost/{run['id']}/post")
    assert again.status_code == 409
    assert "already been posted" in again.json()["detail"]


def test_variance_register_and_export(client):
    ctx = _scenario(client)
    client.post(f"/costing/sales-orders/{ctx['order_id']}/actual-cost", json={})

    adverse = client.get("/costing/variances", params={"adverse_only": True}).json()
    assert adverse and all(Decimal(v["amount"]) > 0 for v in adverse)

    filtered = client.get(
        "/costing/variances", params={"variance_type": "material_usage"}
    ).json()
    assert len(filtered) == 1

    csv = client.get("/costing/variances.csv")
    assert csv.status_code == 200
    assert "text/csv" in csv.headers["content-type"]
    assert "material_usage" in csv.text
    assert "Direction" in csv.text
