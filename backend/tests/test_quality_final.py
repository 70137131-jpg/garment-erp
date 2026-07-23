from decimal import Decimal

import pytest

from app.events import ShipmentDispatched
from app.kernel.events import clear_subscribers, subscribe
from app.quality.aql import AqlError, single_sampling_plan
from tests.factories import (
    make_colour,
    make_customer,
    make_size_range,
    make_style,
)


def test_aql_plan_lookup_level_ii_2_5():
    # Lot 501-1200 → code J, n=80; AQL 2.5 → Ac 5.
    code, n, ac, re_num = single_sampling_plan(1000, Decimal("2.5"))
    assert (code, n, ac, re_num) == ("J", 80, 5, 6)


def test_aql_plan_resolves_arrows():
    # Small lot, AQL 2.5: code A/B are arrows → resolve down to C (n=5, Ac 0).
    code, n, ac, _ = single_sampling_plan(5, Decimal("2.5"))
    assert (code, n, ac) == ("C", 5, 0)


def test_aql_unsupported_rejected():
    with pytest.raises(AqlError):
        single_sampling_plan(1000, Decimal("6.5"))


def test_inline_dhu_endpoint(client):
    sr = make_size_range(client)
    style_id = make_style(client, sr)
    sew = client.post("/production/sewing-orders", json={"style_id": style_id, "planned_qty": 500}).json()
    r = client.post(
        "/quality/inline-inspections",
        json={"sewing_order_id": sew["id"], "units_checked": 200, "defects_found": 6},
    )
    assert r.status_code == 201, r.text
    # DHU = 6/200*100 = 3.0
    assert Decimal(r.json()["dhu"]) == Decimal("3.0000")
    history = client.get("/quality/inline-inspections").json()
    assert len(history) == 1
    assert history[0]["id"] == r.json()["id"]


def _confirmed_order(client):
    sr = make_size_range(client)
    style_id = make_style(client, sr)
    navy = make_colour(client)
    cust = make_customer(client)
    order = client.post(
        "/sales-orders",
        json={
            "customer_id": cust,
            "lines": [
                {"style_id": style_id, "colour_id": navy, "unit_price": "9.50",
                 "sizes": [{"size_label": "M", "ordered_qty": 1000}]},
            ],
        },
    ).json()
    client.post(f"/sales-orders/{order['id']}/confirm")
    return order


def test_final_aql_pass_allows_shipment(client):
    clear_subscribers()
    shipped = []
    subscribe(ShipmentDispatched, lambda e: shipped.append(e))

    order = _confirmed_order(client)
    # Cannot ship before a passed final inspection.
    r = client.post(f"/sales-orders/{order['id']}/ship")
    assert r.status_code == 409

    fi = client.post(
        "/quality/final-inspections",
        json={"sales_order_id": order["id"], "lot_size": 1000, "aql": "2.5", "defects_found": 3},
    )
    assert fi.status_code == 201, fi.text
    assert fi.json()["result"] == "passed"  # 3 <= Ac 5
    history = client.get("/quality/final-inspections").json()
    assert history[0]["id"] == fi.json()["id"]

    r = client.post(f"/sales-orders/{order['id']}/ship")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "shipped"
    assert body["lines"][0]["sizes"][0]["shipped_qty"] == 1000
    assert len(shipped) == 1
    assert shipped[0].invoice_value == Decimal("9500.00")
    clear_subscribers()


def test_final_aql_fail_blocks_shipment(client):
    order = _confirmed_order(client)
    fi = client.post(
        "/quality/final-inspections",
        json={"sales_order_id": order["id"], "lot_size": 1000, "aql": "2.5", "defects_found": 9},
    ).json()
    assert fi["result"] == "failed"  # 9 > Ac 5
    r = client.post(f"/sales-orders/{order['id']}/ship")
    assert r.status_code == 409
