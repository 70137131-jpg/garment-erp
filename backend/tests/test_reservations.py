from decimal import Decimal

from app.inventory.models import Grade, Roll, RollStatus
from app.inventory.service import reserve, roll_available_qty, select_rolls
from app.inventory.models import ReserveRequest
from app.inventory.service import post_movement
from app.inventory.models import MovementType
from tests.factories import make_fabric


def _stock_roll(session, mat, number, length, *, shade="SG-A", grade=Grade.A, width="150",
                status=RollStatus.available, restricted=None):
    roll = Roll(
        roll_number=number, material_id=mat, shade_group=shade, grade=grade,
        width_cm=Decimal(width), length=Decimal(length), status=status,
        restricted_customer_id=restricted,
    )
    session.add(roll)
    session.flush()
    post_movement(session, material_id=mat, movement_type=MovementType.receipt,
                  quantity=Decimal(length), roll_id=roll.id)
    return roll


def test_selection_prefers_tight_single_roll_fit(client, session):
    mat = make_fabric(client)
    _stock_roll(session, mat, "R-BIG", "200")
    _stock_roll(session, mat, "R-FIT", "60")
    session.commit()

    allocs = select_rolls(session, ReserveRequest(material_id=mat, required_qty=Decimal("50")))
    # Both rolls can cover 50; the tighter (60m) is chosen over the 200m roll.
    assert len(allocs) == 1
    assert allocs[0].roll_number == "R-FIT"
    assert allocs[0].qty == Decimal("50.0000")


def test_selection_accumulates_when_no_single_roll_fits(client, session):
    mat = make_fabric(client)
    _stock_roll(session, mat, "R-1", "40")
    _stock_roll(session, mat, "R-2", "40")
    _stock_roll(session, mat, "R-3", "40")
    session.commit()

    allocs = select_rolls(session, ReserveRequest(material_id=mat, required_qty=Decimal("90")))
    assert sum(a.qty for a in allocs) == Decimal("90.0000")
    # FIFO by id: R-1 full, R-2 full, R-3 partial.
    assert [a.roll_number for a in allocs] == ["R-1", "R-2", "R-3"]
    assert allocs[-1].qty == Decimal("10.0000")


def test_selection_respects_grade_priority(client, session):
    mat = make_fabric(client)
    _stock_roll(session, mat, "R-B", "100", grade=Grade.B)
    _stock_roll(session, mat, "R-A", "100", grade=Grade.A)
    session.commit()
    allocs = select_rolls(session, ReserveRequest(material_id=mat, required_qty=Decimal("50")))
    assert allocs[0].roll_number == "R-A"


def test_selection_excludes_customer_restricted_rolls(client, session):
    mat = make_fabric(client)
    _stock_roll(session, mat, "R-OPEN", "30")
    _stock_roll(session, mat, "R-LOCKED", "200", restricted=999)
    session.commit()
    # 30 open + a roll locked to another customer → cannot meet 50.
    try:
        select_rolls(session, ReserveRequest(material_id=mat, required_qty=Decimal("50"), customer_id=1))
        assert False
    except Exception as exc:
        assert "Insufficient" in str(exc)


def test_reserve_reduces_available_and_marks_roll(client, session):
    mat = make_fabric(client)
    roll = _stock_roll(session, mat, "R-1", "100")
    session.commit()

    reserve(session, ReserveRequest(material_id=mat, required_qty=Decimal("100"),
                                    reference_type="cut_order", reference_id=7))
    session.commit()

    session.refresh(roll)
    assert roll.status == RollStatus.reserved
    assert roll_available_qty(session, roll) == Decimal("0.0000")


def test_reservation_endpoints_and_release(client, session):
    mat = make_fabric(client)
    _stock_roll(session, mat, "R-1", "100")
    session.commit()

    r = client.post(
        "/inventory/reservations",
        json={"material_id": mat, "required_qty": "60", "reference_type": "cut_order", "reference_id": 5},
    )
    assert r.status_code == 201, r.text
    res_id = r.json()[0]["id"]

    listed = client.get("/inventory/reservations", params={"reference_id": 5}).json()
    assert len(listed) == 1 and listed[0]["status"] == "active"

    rel = client.post(f"/inventory/reservations/{res_id}/release")
    assert rel.status_code == 200
    assert rel.json()["status"] == "released"
