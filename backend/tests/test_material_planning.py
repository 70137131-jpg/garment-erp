from decimal import Decimal

from tests.factories import (
    make_approved_bom,
    make_colour,
    make_customer,
    make_fabric,
    make_size_range,
    make_style,
)


def _confirmed_order_with_shortage(client):
    size_range = make_size_range(client)
    fabric = make_fabric(client)
    style = make_style(client, size_range)
    make_approved_bom(client, style, fabric, wastage="0.05")
    colour = make_colour(client)
    customer = make_customer(client)
    order = client.post(
        "/sales-orders",
        json={
            "customer_id": customer,
            "currency": "USD",
            "lines": [
                {
                    "style_id": style,
                    "colour_id": colour,
                    "unit_price": "10",
                    "sizes": [{"size_label": "S", "ordered_qty": 10}],
                }
            ],
        },
    )
    assert order.status_code == 201, order.text
    confirmed = client.post(f"/sales-orders/{order.json()['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    return order.json()["id"], fabric


def test_confirmation_persists_shortage_and_generates_requisition(client):
    order_id, fabric = _confirmed_order_with_shortage(client)

    requirements = client.get(f"/sales-orders/{order_id}/material-requirements")
    assert requirements.status_code == 200, requirements.text
    requirement = requirements.json()[0]
    assert Decimal(requirement["required_qty"]) == Decimal("10.5000")
    assert Decimal(requirement["available_qty"]) == Decimal("0.0000")
    assert Decimal(requirement["shortage_qty"]) == Decimal("10.5000")

    requisition = client.post(f"/procurement/purchase-requisitions/from-sales-orders/{order_id}")
    assert requisition.status_code == 201, requisition.text
    body = requisition.json()
    assert body["sales_order_id"] == order_id
    assert body["status"] == "draft"
    assert body["lines"][0]["material_requirement_id"] == requirement["id"]
    assert body["lines"][0]["material_id"] == fabric
    assert Decimal(body["lines"][0]["requested_qty"]) == Decimal("10.5000")


def test_requisition_lines_can_be_consolidated_to_one_purchase_order(client):
    order_id, fabric = _confirmed_order_with_shortage(client)
    generated = client.post(
        f"/procurement/purchase-requisitions/from-sales-orders/{order_id}"
    ).json()
    manual = client.post(
        "/procurement/purchase-requisitions",
        json={"lines": [{"material_id": fabric, "requested_qty": "4.5"}]},
    )
    assert manual.status_code == 201, manual.text
    supplier = client.post(
        "/masters/suppliers", json={"name": "Mill", "material_types": ["fabric"]}
    ).json()["id"]

    purchase_order = client.post(
        "/procurement/purchase-orders/from-requisitions",
        json={
            "supplier_id": supplier,
            "currency": "USD",
            "lines": [
                {
                    "material_id": fabric,
                    "ordered_qty": "15",
                    "unit_price": "3.20",
                    "requisition_line_ids": [
                        generated["lines"][0]["id"], manual.json()["lines"][0]["id"]
                    ],
                }
            ],
        },
    )
    assert purchase_order.status_code == 201, purchase_order.text
    assert Decimal(purchase_order.json()["lines"][0]["ordered_qty"]) == Decimal("15.0000")

    requisitions = client.get("/procurement/purchase-requisitions").json()
    assert {requisition["status"] for requisition in requisitions} == {"ordered"}
