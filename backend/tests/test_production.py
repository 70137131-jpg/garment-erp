from decimal import Decimal

from app.events import ProductionConfirmed
from app.kernel.events import clear_subscribers, subscribe
from app.production.service import compute_efficiency
from tests.factories import (
    make_approved_bom,
    make_colour,
    make_fabric,
    make_size_range,
    make_style,
    make_supplier,
)


def _setup_style_with_bom(client):
    sr = make_size_range(client)
    style_id = make_style(client, sr)
    fabric = make_fabric(client)
    make_approved_bom(client, style_id, fabric)
    colour = make_colour(client)
    return style_id, fabric, colour


def _receive_available_fabric(client, fabric, metres="500", shade="SG-A"):
    """PO → GR → four-point pass, leaving an available roll of `metres`."""
    supplier = make_supplier(client)
    po = client.post(
        "/procurement/purchase-orders",
        json={"supplier_id": supplier, "lines": [{"material_id": fabric, "ordered_qty": metres, "unit_price": "4"}]},
    ).json()
    gr = client.post(
        "/procurement/goods-receipts",
        json={
            "purchase_order_id": po["id"],
            "rolls": [{"purchase_order_line_id": po["lines"][0]["id"], "length": metres,
                       "width_cm": "150", "shade_group": shade}],
        },
    ).json()
    roll_id = gr["rolls"][0]["roll_id"]
    client.post("/quality/four-point-inspections", json={"roll_id": roll_id, "defects": []})
    return roll_id


def test_cut_order_computes_fabric_from_size_bom(client):
    style_id, fabric, colour = _setup_style_with_bom(client)
    r = client.post(
        "/production/cut-orders",
        json={
            "style_id": style_id,
            "colour_id": colour,
            "sizes": [
                {"size_label": "S", "planned_qty": 100},
                {"size_label": "M", "planned_qty": 100},
            ],
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    # BOM S=1.0, M=1.2, wastage 5%: (100*1.0 + 100*1.2) * 1.05 = 231.00
    assert Decimal(body["fabric_required"]) == Decimal("231.0000")
    assert body["status"] == "planned"


def test_full_cut_flow_reserve_issue_complete(client):
    clear_subscribers()
    confirmed = []
    subscribe(ProductionConfirmed, lambda e: confirmed.append(e))

    style_id, fabric, colour = _setup_style_with_bom(client)
    _receive_available_fabric(client, fabric, metres="500")

    cut = client.post(
        "/production/cut-orders",
        json={
            "style_id": style_id,
            "colour_id": colour,
            "sizes": [{"size_label": "S", "planned_qty": 100}, {"size_label": "M", "planned_qty": 100}],
        },
    ).json()
    cut_id = cut["id"]

    r = client.post(f"/production/cut-orders/{cut_id}/reserve-fabric", json={"shade_group": "SG-A"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "fabric_reserved"

    # Issue against the reservation (auto).
    r = client.post(f"/production/cut-orders/{cut_id}/issue-fabric", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "in_cutting"
    assert Decimal(body["fabric_issued"]) == Decimal("231.0000")

    # Ledger consumed 231m: 500 received - 231 issued = 269 on hand.
    bal = client.get("/inventory/balance", params={"material_id": fabric}).json()
    assert Decimal(bal["on_hand"]) == Decimal("269")

    r = client.post(
        f"/production/cut-orders/{cut_id}/complete",
        json={"cut_qty": [{"size_label": "S", "planned_qty": 98}, {"size_label": "M", "planned_qty": 99}]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "completed"
    assert body["pieces_cut"] == 197

    assert len(confirmed) == 1
    assert confirmed[0].pieces == 197
    clear_subscribers()


def test_reserve_fabric_insufficient_stock(client):
    style_id, fabric, colour = _setup_style_with_bom(client)
    _receive_available_fabric(client, fabric, metres="50")  # not enough for 231
    cut = client.post(
        "/production/cut-orders",
        json={
            "style_id": style_id,
            "colour_id": colour,
            "sizes": [{"size_label": "S", "planned_qty": 100}, {"size_label": "M", "planned_qty": 100}],
        },
    ).json()
    r = client.post(f"/production/cut-orders/{cut['id']}/reserve-fabric", json={"shade_group": "SG-A"})
    assert r.status_code == 422
    assert "Insufficient" in r.json()["detail"]


def test_sewing_efficiency_calc():
    # 500 pcs × 12.5 SAM / (25 operators × 480 min) × 100 = 52.08%
    eff = compute_efficiency(500, Decimal("12.5"), 25, 480)
    assert eff == Decimal("52.08")


def test_sewing_order_daily_output_and_average(client):
    style_id, fabric, colour = _setup_style_with_bom(client)
    sew = client.post(
        "/production/sewing-orders",
        json={"style_id": style_id, "line": "Line 1", "planned_qty": 1000},
    ).json()
    assert Decimal(sew["sam"]) == Decimal("12.500000")

    client.post(
        f"/production/sewing-orders/{sew['id']}/daily-output",
        json={"output_date": "2026-07-20", "produced_qty": 500, "operators": 25, "working_minutes": 480},
    )
    r = client.get(f"/production/sewing-orders/{sew['id']}")
    body = r.json()
    assert body["produced_qty"] == 500
    assert body["status"] == "active"
    assert Decimal(body["average_efficiency_pct"]) == Decimal("52.08")


def test_daily_output_idempotent(client):
    style_id, fabric, colour = _setup_style_with_bom(client)
    sew = client.post(
        "/production/sewing-orders",
        json={"style_id": style_id, "planned_qty": 1000},
    ).json()
    payload = {"output_date": "2026-07-20", "produced_qty": 300, "operators": 20,
               "working_minutes": 480, "client_key": "shift-A-2026-07-20"}
    client.post(f"/production/sewing-orders/{sew['id']}/daily-output", json=payload)
    client.post(f"/production/sewing-orders/{sew['id']}/daily-output", json=payload)
    body = client.get(f"/production/sewing-orders/{sew['id']}").json()
    # Counted once despite two posts.
    assert body["produced_qty"] == 300


def test_subcontract_outstanding_balance(client):
    sub = make_supplier(client)
    r = client.post(
        "/production/subcontract-orders",
        json={"subcontractor_id": sub, "process": "Embroidery", "sent_qty": 200, "rate": "0.25"},
    )
    assert r.status_code == 201, r.text
    sc = r.json()
    assert sc["outstanding_qty"] == 200
    assert sc["status"] == "open"

    r = client.post(f"/production/subcontract-orders/{sc['id']}/receive", json={"received_qty": 120})
    body = r.json()
    assert body["received_qty"] == 120
    assert body["outstanding_qty"] == 80
    assert body["status"] == "partially_received"

    r = client.post(f"/production/subcontract-orders/{sc['id']}/receive", json={"received_qty": 80})
    assert r.json()["status"] == "received"

    r = client.post(f"/production/subcontract-orders/{sc['id']}/receive", json={"received_qty": 10})
    assert r.status_code == 422
