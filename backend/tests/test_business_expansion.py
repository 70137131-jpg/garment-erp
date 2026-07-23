from datetime import date
from decimal import Decimal

from app.finance.models import ARInvoice, SettlementStatus
from app.inventory.models import Grade, MovementType, Roll, RollStatus
from app.inventory.service import balance, post_movement
from tests.factories import (
    make_approved_bom,
    make_colour,
    make_customer,
    make_fabric,
    make_size_range,
    make_style,
    make_supplier,
)


def _style_setup(client):
    size_range = make_size_range(client, code="EXP-SIZES")
    colour = make_colour(client, code="EXP-NAVY")
    customer = make_customer(client, name="Expansion Customer")
    fabric = make_fabric(client, name="Expansion Jersey")
    style = make_style(client, size_range, number="EXP-STYLE")
    make_approved_bom(client, style, fabric)
    return customer, colour, fabric, style


def test_full_roll_operations_are_ledger_backed(client, session):
    fabric = make_fabric(client, name="Warehouse Fabric")
    roll = Roll(
        roll_number="WH-ROLL-1",
        material_id=fabric,
        length=Decimal("100"),
        warehouse="MAIN",
        location="A-01",
        grade=Grade.A,
        status=RollStatus.available,
    )
    session.add(roll)
    session.flush()
    post_movement(
        session,
        material_id=fabric,
        roll_id=roll.id,
        movement_type=MovementType.receipt,
        quantity=Decimal("100"),
        warehouse="MAIN",
        location="A-01",
    )
    session.commit()

    split = client.post(
        f"/inventory/rolls/{roll.id}/split",
        json={"parts": [{"quantity": "40"}, {"quantity": "60"}], "reason": "Cut roll"},
    )
    assert split.status_code == 201, split.text
    child_ids = [line["result_roll_id"] for line in split.json()["lines"]]
    assert balance(session, roll_id=roll.id) == 0

    joined = client.post(
        "/inventory/rolls/join",
        json={"roll_ids": child_ids, "reason": "Recombine remnants"},
    )
    assert joined.status_code == 201, joined.text
    result_id = joined.json()["lines"][0]["result_roll_id"]
    assert balance(session, roll_id=result_id) == Decimal("100.0000")

    moved = client.post(
        "/inventory/transfers",
        json={
            "material_id": fabric,
            "roll_id": result_id,
            "quantity": "100",
            "from_warehouse": "MAIN",
            "from_location": "A-01",
            "to_warehouse": "FINISH",
            "to_location": "B-02",
            "reason": "Warehouse transfer",
        },
    )
    assert moved.status_code == 201, moved.text
    assert session.get(Roll, result_id).location == "B-02"

    counted = client.post(
        "/inventory/cycle-counts",
        json={
            "reason": "Monthly count",
            "lines": [{
                "material_id": fabric,
                "roll_id": result_id,
                "warehouse": "FINISH",
                "location": "B-02",
                "counted_qty": "98",
            }],
        },
    )
    assert counted.status_code == 201, counted.text
    assert Decimal(counted.json()["lines"][0]["variance_qty"]) == Decimal("-2")


def test_sales_amendment_history_and_credit_gate(client):
    customer, colour, _, style = _style_setup(client)
    client.patch(f"/masters/customers/{customer}", json={"credit_limit": "50"})
    order = client.post(
        "/sales-orders",
        json={
            "customer_id": customer,
            "currency": "USD",
            "lines": [{
                "style_id": style,
                "colour_id": colour,
                "unit_price": "10",
                "sizes": [{"size_label": "M", "ordered_qty": 10}],
            }],
        },
    ).json()
    amended = client.post(
        f"/sales-orders/{order['id']}/amend",
        json={"notes": "Revised delivery instructions", "reason": "Buyer request"},
    )
    assert amended.status_code == 200, amended.text
    history = client.get(f"/sales-orders/{order['id']}/history").json()
    assert history[0]["action"] == "amended"
    checks = client.get(f"/sales-orders/{order['id']}/commercial-checks").json()
    assert checks["eligible"] is False
    assert client.post(f"/sales-orders/{order['id']}/confirm").status_code == 422
    assert client.post(
        f"/sales-orders/{order['id']}/cancel", json={"reason": "Credit declined"}
    ).status_code == 200


def test_po_approval_supplier_reporting_and_pdf(client):
    supplier = make_supplier(client, name="Approval Supplier")
    fabric = make_fabric(client, name="Approval Fabric")
    po = client.post(
        "/procurement/purchase-orders",
        json={
            "supplier_id": supplier,
            "requires_approval": True,
            "expected_date": str(date.today()),
            "lines": [{"material_id": fabric, "ordered_qty": "20", "unit_price": "2"}],
        },
    ).json()
    assert po["status"] == "pending_approval"
    blocked = client.post(
        "/procurement/goods-receipts",
        json={"purchase_order_id": po["id"], "rolls": [{"purchase_order_line_id": po["lines"][0]["id"], "length": "20"}]},
    )
    assert blocked.status_code == 422
    approved = client.post(
        f"/procurement/purchase-orders/{po['id']}/approval",
        json={"approved": True, "comment": "Budget approved"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["approved_by"]
    pdf = client.get(f"/documents/purchase-orders/{po['id']}/pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")
    performance = client.get("/procurement/supplier-performance")
    assert performance.status_code == 200
    assert performance.json()[0]["purchase_orders"] == 1


def test_production_route_wip_and_lab_management(client):
    _, _, fabric, style = _style_setup(client)
    route = client.post(
        "/production/routes",
        json={
            "style_id": style,
            "name": "Main line",
            "steps": [
                {"sequence": 10, "operation": "Join shoulder", "work_center": "LINE-1"},
                {"sequence": 20, "operation": "Attach neck", "work_center": "LINE-1"},
            ],
        },
    )
    assert route.status_code == 201, route.text
    sewing = client.post(
        "/production/sewing-orders",
        json={"style_id": style, "line": "LINE-1", "planned_qty": 100},
    ).json()
    step_id = route.json()["steps"][0]["id"]
    movement = client.post(
        f"/production/sewing-orders/{sewing['id']}/wip",
        json={"route_step_id": step_id, "quantity_in": 50, "quantity_out": 20},
    )
    assert movement.status_code == 201, movement.text
    summary = client.get(f"/production/sewing-orders/{sewing['id']}/wip").json()
    assert summary[0]["wip_qty"] == 30

    lab = client.post(
        "/quality/lab-tests",
        json={"material_id": fabric, "test_type": "Colour fastness", "method": "ISO 105"},
    )
    assert lab.status_code == 201, lab.text
    completed = client.post(
        f"/quality/lab-tests/{lab.json()['id']}/complete",
        json={"passed": True, "measured_value": "Grade 4"},
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "passed"


def test_financial_reports_and_invoice_pdf(finance_client, session):
    journal = finance_client.post(
        "/finance/journal-entries",
        json={
            "entry_date": "2026-07-01",
            "memo": "Owner capital",
            "lines": [
                {"account_code": "1200", "debit": "1000", "credit": "0"},
                {"account_code": "3000", "debit": "0", "credit": "1000"},
            ],
        },
    )
    assert journal.status_code == 201, journal.text
    trial = finance_client.get("/finance/reports/trial-balance?as_of=2026-07-31")
    assert trial.status_code == 200, trial.text
    assert Decimal(trial.json()["total_debit"]) == Decimal("1000")
    balance_sheet = finance_client.get("/finance/reports/balance-sheet?as_of=2026-07-31")
    assert balance_sheet.status_code == 200
    assert balance_sheet.json()["total_assets"] == balance_sheet.json()["liabilities_and_equity"]
    gl = finance_client.get("/finance/general-ledger?account_code=1200")
    assert gl.status_code == 200
    assert len(gl.json()) == 1

    customer = make_customer(finance_client, name="Invoice Customer")
    invoice = ARInvoice(
        invoice_number="INV-EXP-1",
        customer_id=customer,
        amount=Decimal("250"),
        status=SettlementStatus.open,
        invoice_date=date(2026, 7, 1),
        due_date=date(2026, 7, 15),
    )
    session.add(invoice)
    session.commit()
    session.refresh(invoice)
    aging = finance_client.get("/finance/reports/ar-aging?as_of=2026-07-31")
    assert aging.status_code == 200
    assert Decimal(aging.json()["total"]) == Decimal("250")
    pdf = finance_client.get(f"/documents/invoices/{invoice.id}/pdf")
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")
