"""Part C end-to-end acceptance scenario — the MVP definition-of-done.

Drives the whole order-to-cash spine in one test, exactly as blueprint Part C
describes: TS-100 masters → sales order → procurement → quality → inventory →
production → quality → costing → finance → P&L. Every step asserts the effect
the previous step should have had, so a regression anywhere in the chain fails
here.
"""

from decimal import Decimal

from app.finance.service import account_balance, account_by_code


def _headers(role):
    return {"X-Role": role}


def test_ts100_masters_to_pnl(finance_client, session):
    c = finance_client

    # ------------------------------------------------------------------ #
    # 1. MASTERS — TS-100, Navy/White, size range, versioned BOM, cost sheet
    # ------------------------------------------------------------------ #
    sr = c.post("/masters/size-ranges", json={
        "code": "MENS-STD", "name": "Mens Standard",
        "sizes": [{"position": 1, "label": "S"}, {"position": 2, "label": "M"},
                  {"position": 3, "label": "L"}, {"position": 4, "label": "XL"}],
    }).json()["id"]
    navy = c.post("/masters/colours", json={"code": "NAVY", "name": "Navy"}).json()["id"]
    white = c.post("/masters/colours", json={"code": "WHITE", "name": "White"}).json()["id"]
    customer = c.post("/masters/customers", json={"name": "ACME Apparel"}).json()["id"]
    supplier = c.post("/masters/suppliers", json={"name": "Textile Mills", "material_types": ["fabric"]}).json()["id"]
    fabric = c.post("/masters/materials", json={
        "name": "Single Jersey 180gsm", "material_type": "fabric", "base_uom": "metre",
        "purchase_uom": "roll", "purchase_to_base_factor": "50", "lot_tracked": True, "width_cm": "150",
    }).json()["id"]

    style = c.post("/styles", json={
        "style_number": "TS-100", "description": "Crew Neck Tee", "size_range_id": sr,
        "gender": "mens", "standard_sam": "12.5",
    }, headers=_headers("merchandiser")).json()
    style_id = style["id"]

    cw = c.post(f"/styles/{style_id}/colourways", json={"colour_id": navy, "buyer_reference": "BR-NAVY"},
                headers=_headers("merchandiser")).json()
    c.post(f"/styles/colourways/{cw['id']}/approve-lab-dip", headers=_headers("quality_inspector"))
    c.post(f"/styles/{style_id}/colourways", json={"colour_id": white}, headers=_headers("merchandiser"))

    bom = c.post(f"/styles/{style_id}/bom-versions", json={"lines": [{
        "material_id": fabric, "wastage_pct": "0.05",
        "size_consumption": [{"size_label": "S", "consumption": "1.0"},
                             {"size_label": "M", "consumption": "1.2"},
                             {"size_label": "L", "consumption": "1.4"},
                             {"size_label": "XL", "consumption": "1.6"}],
    }]}, headers=_headers("merchandiser")).json()
    c.post(f"/styles/bom-versions/{bom['id']}/approve", headers=_headers("merchandiser"))

    sheet = c.post(f"/costing/styles/{style_id}/cost-sheets", json={
        "base_size": "M", "sam": "12.5", "sewing_cost_per_min": "0.08", "sewing_efficiency_pct": "80",
        "overhead_pct": "0.15", "margin_pct": "0.20",
        "lines": [{"category": "material", "quantity": "1.2", "rate": "4.00", "wastage_pct": "0.05"}],
    }, headers=_headers("finance")).json()
    c.post(f"/costing/cost-sheets/{sheet['id']}/approve", headers=_headers("finance"))

    # ------------------------------------------------------------------ #
    # 2. SALES ORDER — 2 colours × sizes, confirm
    # ------------------------------------------------------------------ #
    order = c.post("/sales-orders", json={
        "customer_id": customer, "customer_po_number": "PO-778",
        "lines": [
            {"style_id": style_id, "colour_id": navy, "unit_price": "10.00",
             "sizes": [{"size_label": "S", "ordered_qty": 50}, {"size_label": "M", "ordered_qty": 100},
                       {"size_label": "L", "ordered_qty": 100}, {"size_label": "XL", "ordered_qty": 50}]},
            {"style_id": style_id, "colour_id": white, "unit_price": "10.00",
             "sizes": [{"size_label": "M", "ordered_qty": 100}, {"size_label": "L", "ordered_qty": 100}]},
        ],
    }, headers=_headers("merchandiser")).json()
    assert order["total_quantity"] == 500  # navy 300 + white 200
    c.post(f"/sales-orders/{order['id']}/confirm", headers=_headers("merchandiser"))

    # ------------------------------------------------------------------ #
    # 3. PROCUREMENT — buy fabric, post goods receipt (rolls)
    # ------------------------------------------------------------------ #
    po = c.post("/procurement/purchase-orders", json={
        "supplier_id": supplier, "sales_order_id": order["id"],
        "lines": [{"material_id": fabric, "ordered_qty": "1000", "unit_price": "4.00"}],
    }, headers=_headers("procurement")).json()
    po_line = po["lines"][0]["id"]
    gr = c.post("/procurement/goods-receipts", json={
        "purchase_order_id": po["id"], "client_key": "gr-001",
        "rolls": [
            {"purchase_order_line_id": po_line, "length": "500", "width_cm": "150", "dye_lot": "DL-1", "shade_group": "SG-NAVY", "grade": "A"},
            {"purchase_order_line_id": po_line, "length": "500", "width_cm": "150", "dye_lot": "DL-1", "shade_group": "SG-NAVY", "grade": "A"},
        ],
    }, headers=_headers("stores")).json()
    roll_ids = [r["roll_id"] for r in gr["rolls"]]

    # ------------------------------------------------------------------ #
    # 4. QUALITY — four-point inspect; approved rolls become available
    # ------------------------------------------------------------------ #
    for rid in roll_ids:
        res = c.post("/quality/four-point-inspections", json={"roll_id": rid, "defects": [{"penalty_points": 2}]},
                     headers=_headers("quality_inspector")).json()
        assert res["roll_status"] == "available"

    # ------------------------------------------------------------------ #
    # 5-6. PRODUCTION — cut order (fabric from size-BOM), reserve, issue, complete
    # ------------------------------------------------------------------ #
    cut = c.post("/production/cut-orders", json={
        "style_id": style_id, "colour_id": navy, "sales_order_id": order["id"],
        "sizes": [{"size_label": "S", "planned_qty": 50}, {"size_label": "M", "planned_qty": 100},
                  {"size_label": "L", "planned_qty": 100}, {"size_label": "XL", "planned_qty": 50}],
    }, headers=_headers("planner")).json()
    # (50*1.0 + 100*1.2 + 100*1.4 + 50*1.6) * 1.05 = (50+120+140+80)*1.05 = 390*1.05 = 409.5
    assert Decimal(cut["fabric_required"]) == Decimal("409.5000")

    c.post(f"/production/cut-orders/{cut['id']}/reserve-fabric", json={"shade_group": "SG-NAVY"},
           headers=_headers("planner"))
    issued = c.post(f"/production/cut-orders/{cut['id']}/issue-fabric", json={}, headers=_headers("stores")).json()
    assert issued["status"] == "in_cutting"
    assert Decimal(issued["fabric_issued"]) == Decimal("409.5000")

    done = c.post(f"/production/cut-orders/{cut['id']}/complete", json={"cut_qty": [
        {"size_label": "S", "planned_qty": 50}, {"size_label": "M", "planned_qty": 100},
        {"size_label": "L", "planned_qty": 99}, {"size_label": "XL", "planned_qty": 50}]},
        headers=_headers("cutting_supervisor")).json()
    assert done["pieces_cut"] == 299

    # Ledger consumed the fabric: 1000 received - 409.5 issued = 590.5 on hand.
    bal = c.get("/inventory/balance", params={"material_id": fabric}).json()
    assert Decimal(bal["on_hand"]) == Decimal("590.5")

    # Sewing + daily efficiency
    sew = c.post("/production/sewing-orders", json={"style_id": style_id, "line": "Line 1", "planned_qty": 299},
                 headers=_headers("planner")).json()
    c.post(f"/production/sewing-orders/{sew['id']}/daily-output", json={
        "output_date": "2026-07-20", "produced_qty": 299, "operators": 25, "working_minutes": 480},
        headers=_headers("sewing_supervisor"))

    # Subcontract embroidery + reconcile
    sub = c.post("/production/subcontract-orders", json={
        "subcontractor_id": supplier, "process": "Embroidery", "sent_qty": 299, "rate": "0.25"},
        headers=_headers("planner")).json()
    rec = c.post(f"/production/subcontract-orders/{sub['id']}/receive", json={"received_qty": 299},
                 headers=_headers("stores")).json()
    assert rec["status"] == "received" and rec["outstanding_qty"] == 0

    # ------------------------------------------------------------------ #
    # 7. QUALITY — inline DHU, final AQL authorises shipment
    # ------------------------------------------------------------------ #
    c.post("/quality/inline-inspections", json={"sewing_order_id": sew["id"], "units_checked": 100, "defects_found": 3},
           headers=_headers("quality_inspector"))
    fi = c.post("/quality/final-inspections", json={
        "sales_order_id": order["id"], "lot_size": 500, "aql": "2.5", "defects_found": 2},
        headers=_headers("quality_inspector")).json()
    assert fi["result"] == "passed"

    shipped = c.post(f"/sales-orders/{order['id']}/ship", headers=_headers("merchandiser")).json()
    assert shipped["status"] == "shipped"

    # ------------------------------------------------------------------ #
    # 8. COSTING — order profitability vs cost sheet
    # ------------------------------------------------------------------ #
    prof = c.get(f"/costing/sales-orders/{order['id']}/profitability").json()
    assert prof["total_quantity"] == 500
    assert Decimal(prof["revenue"]) == Decimal("5000.00")
    assert Decimal(prof["profit"]) > 0

    # ------------------------------------------------------------------ #
    # 9. FINANCE — AR invoice auto-raised, settle, final P&L
    # ------------------------------------------------------------------ #
    # AP from goods receipt: 1000m * 4.00 = 4000.
    ap = account_by_code(session, "2000")
    assert account_balance(session, ap) == Decimal("4000.00")

    invoices = c.get("/finance/ar-invoices").json()
    assert len(invoices) == 1
    assert Decimal(invoices[0]["amount"]) == Decimal("5000.00")
    c.post(f"/finance/ar-invoices/{invoices[0]['id']}/settle", json={"amount": "5000"},
           headers=_headers("finance"))
    invoices = c.get("/finance/ar-invoices").json()
    assert invoices[0]["status"] == "paid"

    pnl = c.get("/finance/profit-and-loss").json()
    # Revenue and valued production issue are recognised at shipment.
    assert Decimal(pnl["total_income"]) == Decimal("5000.00")
    assert Decimal(pnl["total_expense"]) == Decimal("1638.00")
    assert Decimal(pnl["net_profit"]) == Decimal("3362.00")
