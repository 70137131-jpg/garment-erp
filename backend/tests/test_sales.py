from decimal import Decimal

from app.events import SalesOrderConfirmed
from app.kernel.events import clear_subscribers, subscribe
from tests.factories import make_colour, make_customer, make_size_range, make_style


def _order_payload(client):
    sr = make_size_range(client)
    style_id = make_style(client, sr)
    navy = make_colour(client, "NAVY", "Navy")
    white = make_colour(client, "WHITE", "White")
    cust = make_customer(client)
    return {
        "customer_id": cust,
        "customer_po_number": "PO-778",
        "currency": "USD",
        "lines": [
            {
                "style_id": style_id,
                "colour_id": navy,
                "unit_price": "9.50",
                "sizes": [
                    {"size_label": "S", "ordered_qty": 100},
                    {"size_label": "M", "ordered_qty": 200},
                    {"size_label": "L", "ordered_qty": 150},
                    {"size_label": "XL", "ordered_qty": 50},
                ],
            },
            {
                "style_id": style_id,
                "colour_id": white,
                "unit_price": "9.50",
                "sizes": [
                    {"size_label": "S", "ordered_qty": 80},
                    {"size_label": "M", "ordered_qty": 120},
                ],
            },
        ],
    }


def test_create_order_size_matrix(client):
    r = client.post("/sales-orders", json=_order_payload(client))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["order_number"].startswith("SO-")
    assert body["status"] == "draft"
    # 500 navy + 200 white = 700 pieces.
    assert body["total_quantity"] == 700
    # 700 * 9.50 = 6650.00
    assert Decimal(body["total_value"]) == Decimal("6650.00")
    # Cells preserve size order.
    assert [c["size_label"] for c in body["lines"][0]["sizes"]] == ["S", "M", "L", "XL"]


def test_confirm_emits_event_and_freezes_quantities(client):
    clear_subscribers()
    captured = []
    subscribe(SalesOrderConfirmed, lambda e: captured.append(e))

    created = client.post("/sales-orders", json=_order_payload(client)).json()
    r = client.post(f"/sales-orders/{created['id']}/confirm")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "confirmed"
    # confirmed_qty now mirrors ordered_qty.
    assert body["lines"][0]["sizes"][0]["confirmed_qty"] == 100

    assert len(captured) == 1
    assert captured[0].total_value == Decimal("6650.00")
    assert captured[0].order_number == body["order_number"]
    clear_subscribers()


def test_cannot_confirm_twice(client):
    created = client.post("/sales-orders", json=_order_payload(client)).json()
    client.post(f"/sales-orders/{created['id']}/confirm")
    r = client.post(f"/sales-orders/{created['id']}/confirm")
    assert r.status_code == 409


def test_size_outside_style_range_rejected(client):
    sr = make_size_range(client)
    style_id = make_style(client, sr)
    navy = make_colour(client)
    cust = make_customer(client)
    r = client.post(
        "/sales-orders",
        json={
            "customer_id": cust,
            "lines": [
                {
                    "style_id": style_id,
                    "colour_id": navy,
                    "unit_price": "9.50",
                    "sizes": [{"size_label": "XXXL", "ordered_qty": 10}],
                }
            ],
        },
    )
    assert r.status_code == 422
