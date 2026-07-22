from decimal import Decimal

from app.inventory.models import MovementType, Roll, RollStatus
from app.inventory.service import balance, post_movement, query_rolls


def _make_fabric(client):
    return client.post(
        "/masters/materials",
        json={"name": "Twill 240gsm", "material_type": "fabric", "base_uom": "metre"},
    ).json()["id"]


def test_balance_is_sum_of_ledger(client, session):
    mat = _make_fabric(client)
    post_movement(session, material_id=mat, movement_type=MovementType.receipt, quantity=Decimal("100"))
    post_movement(session, material_id=mat, movement_type=MovementType.issue, quantity=Decimal("30"))
    post_movement(session, material_id=mat, movement_type=MovementType.return_to_stock, quantity=Decimal("5"))
    session.commit()
    assert balance(session, material_id=mat) == Decimal("75.0000")


def test_issue_beyond_stock_rejected(client, session):
    mat = _make_fabric(client)
    post_movement(session, material_id=mat, movement_type=MovementType.receipt, quantity=Decimal("10"))
    session.commit()
    try:
        post_movement(session, material_id=mat, movement_type=MovementType.issue, quantity=Decimal("20"))
        assert False, "should have raised"
    except Exception as exc:
        assert "Insufficient" in str(exc)


def test_adjustment_endpoint_and_ledger_immutable_history(client):
    mat = _make_fabric(client)
    r = client.post(
        "/inventory/adjustments",
        json={"material_id": mat, "quantity": "250", "note": "Opening balance"},
    )
    assert r.status_code == 201, r.text
    r = client.post(
        "/inventory/adjustments",
        json={"material_id": mat, "quantity": "-40", "note": "Damaged"},
    )
    assert r.status_code == 201, r.text
    # Two immutable lines remain; balance nets to 210.
    ledger = client.get("/inventory/ledger", params={"material_id": mat}).json()
    assert len(ledger) == 2
    bal = client.get("/inventory/balance", params={"material_id": mat}).json()
    assert Decimal(bal["on_hand"]) == Decimal("210")


def test_roll_register_queries(client, session):
    mat = _make_fabric(client)
    session.add_all(
        [
            Roll(roll_number="R-1", material_id=mat, shade_group="SG-A", grade="A",
                 width_cm=Decimal("150"), length=Decimal("50"), status=RollStatus.available),
            Roll(roll_number="R-2", material_id=mat, shade_group="SG-A", grade="B",
                 width_cm=Decimal("140"), length=Decimal("48"), status=RollStatus.available),
            Roll(roll_number="R-3", material_id=mat, shade_group="SG-B", grade="A",
                 width_cm=Decimal("150"), length=Decimal("52"), status=RollStatus.quarantined),
        ]
    )
    session.commit()

    same_shade = query_rolls(session, material_id=mat, shade_group="SG-A")
    assert {r.roll_number for r in same_shade} == {"R-1", "R-2"}

    grade_a_available = query_rolls(session, material_id=mat, grade="A", status=RollStatus.available)
    assert {r.roll_number for r in grade_a_available} == {"R-1"}

    wide = query_rolls(session, material_id=mat, min_width_cm=Decimal("150"))
    assert {r.roll_number for r in wide} == {"R-1", "R-3"}
