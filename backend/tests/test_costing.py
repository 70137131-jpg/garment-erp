from decimal import Decimal

from tests.factories import make_colour, make_customer, make_size_range, make_style


def _cost_sheet_payload():
    return {
        "base_size": "M",
        "currency": "USD",
        "sam": "12.5",
        "sewing_cost_per_min": "0.08",
        "sewing_efficiency_pct": "80",
        "overhead_pct": "0.15",
        "margin_pct": "0.20",
        "lines": [
            {"category": "material", "description": "Shell fabric", "quantity": "1.5", "rate": "4.00", "wastage_pct": "0.05"},
            {"category": "trim", "description": "Buttons", "quantity": "6", "rate": "0.05"},
        ],
    }


def test_cost_sheet_rollup(client):
    sr = make_size_range(client)
    style_id = make_style(client, sr)
    r = client.post(f"/costing/styles/{style_id}/cost-sheets", json=_cost_sheet_payload())
    assert r.status_code == 201, r.text
    body = r.json()

    # material: 1.5*4.00*1.05 = 6.30 ; trim: 6*0.05 = 0.30 → material_cost 6.60
    assert Decimal(body["material_cost"]) == Decimal("6.60")
    # sewing: 12.5 * 0.08 / 0.80 = 1.25
    assert Decimal(body["sewing_cost"]) == Decimal("1.25")
    # overhead: 0.15 * (6.60 + 1.25) = 1.1775 → 1.18
    assert Decimal(body["overhead_cost"]) == Decimal("1.18")
    # total: 6.60 + 1.25 + 1.18 = 9.03
    assert Decimal(body["total_cost"]) == Decimal("9.03")
    # price: 9.03 / (1 - 0.20) = 11.2875 → 11.29
    assert Decimal(body["selling_price"]) == Decimal("11.29")


def test_cost_sheet_versioning(client):
    sr = make_size_range(client)
    style_id = make_style(client, sr)
    v1 = client.post(f"/costing/styles/{style_id}/cost-sheets", json=_cost_sheet_payload()).json()
    assert v1["version_no"] == 1
    r = client.post(f"/costing/cost-sheets/{v1['id']}/approve")
    assert r.json()["status"] == "approved"

    v2 = client.post(f"/costing/styles/{style_id}/cost-sheets", json=_cost_sheet_payload()).json()
    client.post(f"/costing/cost-sheets/{v2['id']}/approve")
    v1_now = client.get(f"/costing/cost-sheets/{v1['id']}").json()
    assert v1_now["status"] == "superseded"


def test_order_profitability(client):
    sr = make_size_range(client)
    style_id = make_style(client, sr)
    navy = make_colour(client)
    cust = make_customer(client)

    sheet = client.post(f"/costing/styles/{style_id}/cost-sheets", json=_cost_sheet_payload()).json()
    client.post(f"/costing/cost-sheets/{sheet['id']}/approve")

    order = client.post(
        "/sales-orders",
        json={
            "customer_id": cust,
            "lines": [
                {"style_id": style_id, "colour_id": navy, "unit_price": "11.29",
                 "sizes": [{"size_label": "M", "ordered_qty": 1000}]},
            ],
        },
    ).json()
    client.post(f"/sales-orders/{order['id']}/confirm")

    r = client.get(f"/costing/sales-orders/{order['id']}/profitability")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_quantity"] == 1000
    assert Decimal(body["revenue"]) == Decimal("11290.00")
    # cost 9.03 * 1000 = 9030.00 ; profit = 2260.00
    assert Decimal(body["total_cost"]) == Decimal("9030.00")
    assert Decimal(body["profit"]) == Decimal("2260.00")
    assert body["cost_sheet_version_id"] == sheet["id"]
