from decimal import Decimal

from app.inventory.models import MovementType
from app.inventory.service import post_movement, stock_value
from tests.factories import make_colour, make_customer, make_fabric, make_size_range, make_style


def _confirmed_order(client):
    size_range = make_size_range(client)
    style = make_style(client, size_range)
    colour = make_colour(client)
    customer = make_customer(client)
    order = client.post(
        "/sales-orders",
        json={
            "customer_id": customer,
            "lines": [{
                "style_id": style, "colour_id": colour, "unit_price": "10",
                "sizes": [{"size_label": "S", "ordered_qty": 100}],
            }],
        },
    ).json()
    assert client.post(f"/sales-orders/{order['id']}/confirm").status_code == 200
    inspection = client.post(
        "/quality/final-inspections",
        json={"sales_order_id": order["id"], "lot_size": 100, "defects_found": 0},
    )
    assert inspection.status_code == 201, inspection.text
    return order


def test_partial_shipments_track_size_quantities_and_close_only_when_complete(client):
    order = _confirmed_order(client)
    cell_id = client.get(f"/sales-orders/{order['id']}").json()["lines"][0]["sizes"][0]["id"]

    partial = client.post(
        f"/sales-orders/{order['id']}/ship",
        json={"shipping_reference": "AWB-001", "lines": [{
            "sales_order_size_cell_id": cell_id, "quantity": 30, "carton_count": 2,
        }]},
    )
    assert partial.status_code == 200, partial.text
    assert partial.json()["status"] == "partially_shipped"
    assert partial.json()["lines"][0]["sizes"][0]["shipped_qty"] == 30
    shipment = client.get(f"/sales-orders/{order['id']}/shipments").json()[0]
    assert shipment["total_quantity"] == 30
    assert shipment["total_cartons"] == 2

    assert client.post(f"/sales-orders/{order['id']}/close", json={"reason": "Complete"}).status_code == 409
    final = client.post(f"/sales-orders/{order['id']}/ship")
    assert final.status_code == 200, final.text
    assert final.json()["status"] == "shipped"
    closed = client.post(f"/sales-orders/{order['id']}/close", json={"reason": "Delivered"})
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "closed"


def test_fifo_and_weighted_average_valuation(session, client):
    fifo = client.post(
        "/masters/materials",
        json={"name": "FIFO fabric", "material_type": "fabric", "valuation_method": "fifo"},
    ).json()["id"]
    post_movement(session, material_id=fifo, movement_type=MovementType.receipt, quantity=Decimal("10"), unit_cost=Decimal("3"))
    post_movement(session, material_id=fifo, movement_type=MovementType.receipt, quantity=Decimal("10"), unit_cost=Decimal("5"))
    issue = post_movement(session, material_id=fifo, movement_type=MovementType.issue, quantity=Decimal("12"))
    assert issue.unit_cost == Decimal("3.33")
    assert stock_value(session, material_id=fifo) == Decimal("40.00")

    average = client.post(
        "/masters/materials",
        json={"name": "Average fabric", "material_type": "fabric", "valuation_method": "weighted_average"},
    ).json()["id"]
    post_movement(session, material_id=average, movement_type=MovementType.receipt, quantity=Decimal("10"), unit_cost=Decimal("3"))
    post_movement(session, material_id=average, movement_type=MovementType.receipt, quantity=Decimal("10"), unit_cost=Decimal("5"))
    issue = post_movement(session, material_id=average, movement_type=MovementType.issue, quantity=Decimal("12"))
    assert issue.unit_cost == Decimal("4.00")
    assert stock_value(session, material_id=average) == Decimal("32.00")
