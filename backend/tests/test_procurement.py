from decimal import Decimal

from app.events import GoodsReceiptPosted, SupplierInvoiceRaised
from app.kernel.events import clear_subscribers, subscribe
from tests.factories import make_fabric, make_supplier


def _po(client, ordered="500"):
    supplier = make_supplier(client)
    fabric = make_fabric(client)
    r = client.post(
        "/procurement/purchase-orders",
        json={
            "supplier_id": supplier,
            "currency": "USD",
            "lines": [
                {"material_id": fabric, "ordered_qty": ordered, "unit_price": "4.00"}
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json(), fabric, supplier


def test_create_po_tracks_outstanding(client):
    po, _, _ = _po(client)
    assert po["order_number"].startswith("PO-")
    assert po["status"] == "issued"
    assert Decimal(po["total_value"]) == Decimal("2000.00")
    assert Decimal(po["lines"][0]["outstanding_qty"]) == Decimal("500")


def test_goods_receipt_creates_rolls_moves_stock_updates_po(client):
    clear_subscribers()
    gr_events, ap_events = [], []
    subscribe(GoodsReceiptPosted, lambda e: gr_events.append(e))
    subscribe(SupplierInvoiceRaised, lambda e: ap_events.append(e))

    po, fabric, supplier = _po(client, ordered="500")
    line_id = po["lines"][0]["id"]

    r = client.post(
        "/procurement/goods-receipts",
        json={
            "purchase_order_id": po["id"],
            "rolls": [
                {"purchase_order_line_id": line_id, "length": "120", "width_cm": "150",
                 "dye_lot": "DL-1", "shade_group": "SG-A", "grade": "A"},
                {"purchase_order_line_id": line_id, "length": "130", "width_cm": "150",
                 "dye_lot": "DL-1", "shade_group": "SG-A", "grade": "A"},
            ],
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["receipt_number"].startswith("GR-")
    assert len(body["rolls"]) == 2
    # Rolls arrive pending inspection.
    assert all(roll["status"] == "pending_inspection" for roll in body["rolls"])
    assert Decimal(body["total_length"]) == Decimal("250")

    # Stock ledger reflects 250m received.
    bal = client.get("/inventory/balance", params={"material_id": fabric}).json()
    assert Decimal(bal["on_hand"]) == Decimal("250")

    # PO line received 250 of 500 → partially received.
    po_now = client.get(f"/procurement/purchase-orders/{po['id']}").json()
    assert po_now["status"] == "partially_received"
    assert Decimal(po_now["lines"][0]["received_qty"]) == Decimal("250")
    assert Decimal(po_now["lines"][0]["outstanding_qty"]) == Decimal("250")

    # Events fired with the received value (250 * 4.00).
    assert len(gr_events) == 1 and gr_events[0].total_value == Decimal("1000.00")
    assert len(ap_events) == 1 and ap_events[0].amount == Decimal("1000.00")
    clear_subscribers()


def test_full_receipt_marks_po_received(client):
    po, fabric, _ = _po(client, ordered="200")
    line_id = po["lines"][0]["id"]
    client.post(
        "/procurement/goods-receipts",
        json={
            "purchase_order_id": po["id"],
            "rolls": [{"purchase_order_line_id": line_id, "length": "200", "width_cm": "150"}],
        },
    )
    po_now = client.get(f"/procurement/purchase-orders/{po['id']}").json()
    assert po_now["status"] == "received"


def test_goods_receipt_idempotent_on_client_key(client):
    po, fabric, _ = _po(client, ordered="500")
    line_id = po["lines"][0]["id"]
    payload = {
        "purchase_order_id": po["id"],
        "client_key": "scan-batch-001",
        "rolls": [{"purchase_order_line_id": line_id, "length": "100", "width_cm": "150"}],
    }
    r1 = client.post("/procurement/goods-receipts", json=payload)
    r2 = client.post("/procurement/goods-receipts", json=payload)
    assert r1.json()["id"] == r2.json()["id"]
    # Stock only moved once despite two calls.
    bal = client.get("/inventory/balance", params={"material_id": fabric}).json()
    assert Decimal(bal["on_hand"]) == Decimal("100")


def test_four_point_pass_makes_roll_available(client):
    po, fabric, _ = _po(client, ordered="500")
    line_id = po["lines"][0]["id"]
    gr = client.post(
        "/procurement/goods-receipts",
        json={
            "purchase_order_id": po["id"],
            "rolls": [{"purchase_order_line_id": line_id, "length": "100", "width_cm": "150"}],
        },
    ).json()
    roll_id = gr["rolls"][0]["roll_id"]

    # A couple of minor defects, well under threshold → pass → available.
    r = client.post(
        "/quality/four-point-inspections",
        json={
            "roll_id": roll_id,
            "defects": [
                {"penalty_points": 2, "description": "slub"},
                {"penalty_points": 1, "description": "knot"},
            ],
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["result"] == "passed"
    assert body["roll_status"] == "available"


def test_four_point_fail_quarantines_roll(client):
    po, fabric, _ = _po(client, ordered="500")
    line_id = po["lines"][0]["id"]
    gr = client.post(
        "/procurement/goods-receipts",
        json={
            "purchase_order_id": po["id"],
            # short, narrow roll so a few points blow past the threshold
            "rolls": [{"purchase_order_line_id": line_id, "length": "5", "width_cm": "100"}],
        },
    ).json()
    roll_id = gr["rolls"][0]["roll_id"]

    r = client.post(
        "/quality/four-point-inspections",
        json={
            "roll_id": roll_id,
            "defects": [{"penalty_points": 4} for _ in range(5)],
        },
    ).json()
    assert r["result"] == "failed"
    assert r["roll_status"] == "quarantined"


def test_cannot_reinspect_available_roll(client):
    po, fabric, _ = _po(client, ordered="500")
    line_id = po["lines"][0]["id"]
    gr = client.post(
        "/procurement/goods-receipts",
        json={
            "purchase_order_id": po["id"],
            "rolls": [{"purchase_order_line_id": line_id, "length": "100", "width_cm": "150"}],
        },
    ).json()
    roll_id = gr["rolls"][0]["roll_id"]
    client.post("/quality/four-point-inspections", json={"roll_id": roll_id, "defects": []})
    r = client.post("/quality/four-point-inspections", json={"roll_id": roll_id, "defects": []})
    assert r.status_code == 422
