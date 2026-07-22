from decimal import Decimal

from app.finance.service import (
    account_balance,
    account_by_code,
    profit_and_loss,
    seed_chart_of_accounts,
)
from tests.factories import (
    make_colour,
    make_customer,
    make_fabric,
    make_size_range,
    make_style,
    make_supplier,
)


def test_manual_journal_requires_balance(finance_client):
    r = finance_client.post(
        "/finance/journal-entries",
        json={"memo": "unbalanced", "lines": [
            {"account_code": "1000", "debit": "100"},
            {"account_code": "2000", "credit": "90"},
        ]},
    )
    assert r.status_code == 422
    assert "not balanced" in r.json()["detail"].lower()


def test_manual_journal_and_reversal(finance_client):
    r = finance_client.post(
        "/finance/journal-entries",
        json={"memo": "opening", "lines": [
            {"account_code": "1200", "debit": "1000"},
            {"account_code": "3000", "credit": "1000"},
        ]},
    )
    assert r.status_code == 201, r.text
    entry = r.json()
    assert entry["status"] == "posted"
    assert Decimal(entry["total_debit"]) == Decimal("1000.00")

    rev = finance_client.post(f"/finance/journal-entries/{entry['id']}/reverse")
    assert rev.status_code == 200
    assert rev.json()["memo"].startswith("Reversal of")
    # Cannot reverse twice.
    again = finance_client.post(f"/finance/journal-entries/{entry['id']}/reverse")
    assert again.status_code == 409


def test_goods_receipt_auto_posts_ap_and_inventory(finance_client, session):
    supplier = make_supplier(finance_client)
    fabric = make_fabric(finance_client)
    po = finance_client.post(
        "/procurement/purchase-orders",
        json={"supplier_id": supplier, "lines": [{"material_id": fabric, "ordered_qty": "100", "unit_price": "4"}]},
    ).json()
    finance_client.post(
        "/procurement/goods-receipts",
        json={"purchase_order_id": po["id"], "rolls": [
            {"purchase_order_line_id": po["lines"][0]["id"], "length": "100", "width_cm": "150"}]},
    )

    # Inventory debit 400, AP credit 400.
    inv = account_by_code(session, "1000")
    ap = account_by_code(session, "2000")
    assert account_balance(session, inv) == Decimal("400.00")
    assert account_balance(session, ap) == Decimal("400.00")

    # An AP bill was raised.
    bills = finance_client.get("/finance/ap-bills").json()
    assert len(bills) == 1
    assert Decimal(bills[0]["outstanding"]) == Decimal("400.00")


def _order_to_shipment(finance_client):
    sr = make_size_range(finance_client)
    style_id = make_style(finance_client, sr)
    navy = make_colour(finance_client)
    cust = make_customer(finance_client)
    order = finance_client.post(
        "/sales-orders",
        json={"customer_id": cust, "lines": [
            {"style_id": style_id, "colour_id": navy, "unit_price": "10.00",
             "sizes": [{"size_label": "M", "ordered_qty": 500}]}]},
    ).json()
    finance_client.post(f"/sales-orders/{order['id']}/confirm")
    finance_client.post(
        "/quality/final-inspections",
        json={"sales_order_id": order["id"], "lot_size": 500, "aql": "2.5", "defects_found": 0},
    )
    finance_client.post(f"/sales-orders/{order['id']}/ship")
    return order


def test_shipment_auto_posts_ar_and_revenue_and_pnl(finance_client, session):
    _order_to_shipment(finance_client)

    ar = account_by_code(session, "1100")
    sales = account_by_code(session, "4000")
    assert account_balance(session, ar) == Decimal("5000.00")
    assert account_balance(session, sales) == Decimal("5000.00")

    invoices = finance_client.get("/finance/ar-invoices").json()
    assert len(invoices) == 1
    inv_id = invoices[0]["id"]

    # Settle half.
    finance_client.post(f"/finance/ar-invoices/{inv_id}/settle", json={"amount": "2000"})
    invoices = finance_client.get("/finance/ar-invoices").json()
    assert invoices[0]["status"] == "part_paid"
    assert Decimal(invoices[0]["outstanding"]) == Decimal("3000.00")

    pnl = finance_client.get("/finance/profit-and-loss").json()
    assert Decimal(pnl["total_income"]) == Decimal("5000.00")
    assert Decimal(pnl["net_profit"]) == Decimal("5000.00")


def test_operations_work_without_finance_configured(client):
    """A bare DB (no CoA) must not break goods receipt — finance no-ops."""
    supplier = make_supplier(client)
    fabric = make_fabric(client)
    po = client.post(
        "/procurement/purchase-orders",
        json={"supplier_id": supplier, "lines": [{"material_id": fabric, "ordered_qty": "100", "unit_price": "4"}]},
    ).json()
    r = client.post(
        "/procurement/goods-receipts",
        json={"purchase_order_id": po["id"], "rolls": [
            {"purchase_order_line_id": po["lines"][0]["id"], "length": "100", "width_cm": "150"}]},
    )
    assert r.status_code == 201
