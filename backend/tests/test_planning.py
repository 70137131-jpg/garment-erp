"""Module 11 — MRP, capacity, and available-to-promise.

The behaviour that matters most here is *sequential netting*: MRP must consume a
running balance across the whole horizon, so a second order cannot be told the
same stock is available that a first order already claimed. The existing
per-order shortage calculation deliberately does not do this, and the contrast
is asserted directly in ``test_mrp_nets_sequentially_unlike_per_order_shortage``.
"""

from datetime import date, timedelta
from decimal import Decimal

from tests.factories import (
    make_approved_bom,
    make_colour,
    make_customer,
    make_fabric,
    make_size_range,
    make_style,
    make_supplier,
)


def _today():
    return date.today()


def _iso(days_from_today: int) -> str:
    return (_today() + timedelta(days=days_from_today)).isoformat()


def _order(client, customer, style_id, colour, qty, delivery_in_days, confirm=True):
    order = client.post("/sales-orders", json={
        "customer_id": customer,
        "lines": [{
            "style_id": style_id, "colour_id": colour, "unit_price": "10.00",
            "delivery_date": _iso(delivery_in_days),
            "sizes": [{"size_label": "M", "ordered_qty": qty}],
        }],
    }).json()
    if confirm:
        client.post(f"/sales-orders/{order['id']}/confirm")
    return order


def _base(client, lead_time_days=0, min_order_qty="0"):
    sr = make_size_range(client)
    style_id = make_style(client, sr)
    colour = make_colour(client)
    customer = make_customer(client)
    fabric = client.post("/masters/materials", json={
        "name": "Single Jersey", "material_type": "fabric", "base_uom": "metre",
        "purchase_uom": "roll", "purchase_to_base_factor": "50", "lot_tracked": True,
        "width_cm": "150", "lead_time_days": lead_time_days,
        "min_order_qty": min_order_qty,
    }).json()["id"]
    make_approved_bom(client, style_id, fabric, wastage="0")
    return {"style_id": style_id, "colour": colour, "customer": customer, "fabric": fabric}


# --------------------------------------------------------------------------- #
# MRP
# --------------------------------------------------------------------------- #
def test_mrp_creates_planned_orders_for_uncovered_demand(client):
    ctx = _base(client)
    # 100 pcs at size M = 1.2 m each, no wastage → 120 m required.
    _order(client, ctx["customer"], ctx["style_id"], ctx["colour"], 100, 10)

    run = client.post("/planning/mrp-runs", json={"bucket_days": 7}).json()
    assert run["planned_order_count"] == 1

    detail = client.get(f"/planning/mrp-runs/{run['id']}").json()
    demand_buckets = [b for b in detail["buckets"] if Decimal(b["gross_requirement"]) > 0]
    assert len(demand_buckets) == 1
    bucket = demand_buckets[0]
    assert Decimal(bucket["gross_requirement"]) == Decimal("120.0000")
    assert Decimal(bucket["net_requirement"]) == Decimal("120.0000")
    assert Decimal(bucket["planned_order_qty"]) == Decimal("120.0000")
    # Nothing left over once the planned order lands.
    assert Decimal(bucket["projected_available"]) == Decimal("0.0000")

    # Traceability back to the order that caused it.
    assert bucket["demand_sources"][0]["quantity"] == "120.0000"


def test_mrp_nets_sequentially_unlike_per_order_shortage(client, session):
    """Two orders must not both be told the same 150 m is available.

    The per-order calculation reports zero shortage for *both* orders because
    each nets against total stock independently. MRP walks a running balance,
    so the second order sees only what the first left behind.
    """
    ctx = _base(client)
    from app.inventory.models import MovementType
    from app.inventory.service import post_movement

    post_movement(
        session, material_id=ctx["fabric"], movement_type=MovementType.receipt,
        quantity=Decimal("150"), unit_cost=Decimal("4.00"),
        reference_type="opening", actor="test",
    )
    session.commit()

    # Each order needs 120 m; together 240 m against 150 m on hand.
    first = _order(client, ctx["customer"], ctx["style_id"], ctx["colour"], 100, 10)
    second = _order(client, ctx["customer"], ctx["style_id"], ctx["colour"], 100, 20)

    # Per-order view: both see 150 m and report no shortage.
    for order in (first, second):
        reqs = client.get(f"/sales-orders/{order['id']}/material-requirements").json()
        assert Decimal(reqs[0]["shortage_qty"]) == Decimal("0.0000")

    # MRP view: 240 m of demand against 150 m of supply → 90 m must be bought.
    run = client.post("/planning/mrp-runs", json={"bucket_days": 7}).json()
    detail = client.get(f"/planning/mrp-runs/{run['id']}").json()
    planned_total = sum(Decimal(p["quantity"]) for p in detail["planned_orders"])
    assert planned_total == Decimal("90.0000")


def test_lead_time_offsets_release_and_flags_past_due(client):
    ctx = _base(client, lead_time_days=30)
    # Needed in 10 days but the material takes 30 → release date is in the past.
    _order(client, ctx["customer"], ctx["style_id"], ctx["colour"], 100, 10)

    run = client.post("/planning/mrp-runs", json={"bucket_days": 7}).json()
    assert run["past_due_count"] == 1

    planned = client.get(f"/planning/mrp-runs/{run['id']}/planned-orders").json()[0]
    assert planned["lead_time_days"] == 30
    need = date.fromisoformat(planned["need_date"])
    release = date.fromisoformat(planned["release_date"])
    assert (need - release).days == 30
    assert planned["past_due"] is True


def test_minimum_order_quantity_rounds_the_planned_order_up(client):
    ctx = _base(client, min_order_qty="500")
    _order(client, ctx["customer"], ctx["style_id"], ctx["colour"], 100, 10)

    run = client.post("/planning/mrp-runs", json={"bucket_days": 7}).json()
    detail = client.get(f"/planning/mrp-runs/{run['id']}").json()
    planned = detail["planned_orders"][0]
    # Needs 120 m, supplier minimum is 500 m.
    assert Decimal(planned["quantity"]) == Decimal("500.0000")
    bucket = [b for b in detail["buckets"] if Decimal(b["planned_order_qty"]) > 0][0]
    # The surplus carries forward rather than vanishing.
    assert Decimal(bucket["projected_available"]) == Decimal("380.0000")


def test_scheduled_receipts_reduce_the_net_requirement(client):
    ctx = _base(client)
    supplier = make_supplier(client)
    _order(client, ctx["customer"], ctx["style_id"], ctx["colour"], 100, 20)

    # An open PO arriving before the need date covers the demand.
    client.post("/procurement/purchase-orders", json={
        "supplier_id": supplier, "expected_date": _iso(10),
        "lines": [{"material_id": ctx["fabric"], "ordered_qty": "200", "unit_price": "4.00"}],
    })

    run = client.post("/planning/mrp-runs", json={"bucket_days": 30}).json()
    assert run["planned_order_count"] == 0
    detail = client.get(f"/planning/mrp-runs/{run['id']}").json()
    covered = [b for b in detail["buckets"] if Decimal(b["scheduled_receipts"]) > 0][0]
    assert Decimal(covered["scheduled_receipts"]) == Decimal("200.0000")
    assert Decimal(covered["net_requirement"]) == Decimal("0.0000")


def test_firming_creates_a_requisition_and_is_one_way(client):
    ctx = _base(client)
    _order(client, ctx["customer"], ctx["style_id"], ctx["colour"], 100, 10)
    run = client.post("/planning/mrp-runs", json={}).json()

    firmed = client.post(f"/planning/mrp-runs/{run['id']}/firm", json={})
    assert firmed.status_code == 201, firmed.text
    body = firmed.json()
    assert body["requisition_number"].startswith("PR-")
    assert body["run_status"] == "firmed"

    planned = client.get(f"/planning/mrp-runs/{run['id']}/planned-orders").json()[0]
    assert planned["status"] == "firmed"
    assert planned["requisition_id"] == body["requisition_id"]

    # Nothing left in a planned state, so a second firm has nothing to do.
    again = client.post(f"/planning/mrp-runs/{run['id']}/firm", json={})
    assert again.status_code == 422
    assert "no planned orders" in again.json()["detail"].lower()


def test_mrp_requires_something_to_plan(client):
    _base(client)
    r = client.post("/planning/mrp-runs", json={})
    assert r.status_code == 422
    assert "nothing to plan" in r.json()["detail"].lower()


def test_mrp_export(client):
    ctx = _base(client)
    _order(client, ctx["customer"], ctx["style_id"], ctx["colour"], 100, 10)
    run = client.post("/planning/mrp-runs", json={}).json()
    csv = client.get(f"/planning/mrp-runs/{run['id']}/export")
    assert csv.status_code == 200
    assert "text/csv" in csv.headers["content-type"]
    assert "Projected available" in csv.text


# --------------------------------------------------------------------------- #
# Capacity
# --------------------------------------------------------------------------- #
def _centre(client, code="SEW-1", operators=25, efficiency="80"):
    return client.post("/planning/work-centres", json={
        "code": code, "name": "Sewing line 1", "centre_type": "sewing",
        "operators": operators, "shift_minutes": 480, "shifts_per_day": 1,
        "efficiency_pct": efficiency,
    }).json()


def test_work_centre_capacity_is_derated_by_efficiency(client):
    centre = _centre(client)
    # 25 × 480 × 1 × 80% = 9600 minutes a day.
    assert Decimal(centre["daily_minutes"]) == Decimal("9600.0000")


def test_duplicate_work_centre_code_is_rejected(client):
    _centre(client)
    assert _centre_status(client) == 409


def _centre_status(client):
    return client.post("/planning/work-centres", json={
        "code": "SEW-1", "name": "Duplicate", "centre_type": "sewing",
        "operators": 1, "shift_minutes": 480,
    }).status_code


def test_capacity_board_reports_load_and_overload(client):
    centre = _centre(client)
    # One week of capacity = 7 × 9600 = 67,200 minutes.
    client.post("/planning/capacity-bookings", json={
        "work_centre_id": centre["id"], "minutes": "33600",
        "start_date": _iso(0), "end_date": _iso(6), "description": "Order A",
    })

    board = client.get("/planning/capacity-board", params={
        "horizon_start": _iso(0), "horizon_end": _iso(6), "bucket_days": 7,
    }).json()
    bucket = board["centres"][0]["buckets"][0]
    assert Decimal(bucket["capacity_minutes"]) == Decimal("67200.0000")
    assert Decimal(bucket["loaded_minutes"]) == Decimal("33600.0000")
    assert Decimal(bucket["utilisation_pct"]) == Decimal("50.00")
    assert bucket["overloaded"] is False

    # Double-book the line into overload.
    client.post("/planning/capacity-bookings", json={
        "work_centre_id": centre["id"], "minutes": "40000",
        "start_date": _iso(0), "end_date": _iso(6), "description": "Order B",
    })
    board = client.get("/planning/capacity-board", params={
        "horizon_start": _iso(0), "horizon_end": _iso(6), "bucket_days": 7,
    }).json()
    bucket = board["centres"][0]["buckets"][0]
    assert bucket["overloaded"] is True
    assert board["centres"][0]["overloaded_buckets"] == 1


def test_capacity_exception_overrides_nominal_capacity(client):
    centre = _centre(client)
    client.post("/planning/capacity-exceptions", json={
        "work_centre_id": centre["id"], "exception_date": _iso(0),
        "available_minutes": "0", "reason": "Public holiday",
    })
    board = client.get("/planning/capacity-board", params={
        "horizon_start": _iso(0), "horizon_end": _iso(0), "bucket_days": 1,
    }).json()
    assert Decimal(board["centres"][0]["buckets"][0]["capacity_minutes"]) == Decimal("0.0000")


def test_capacity_exception_is_one_row_per_day(client):
    centre = _centre(client)
    for minutes in ("0", "4800"):
        client.post("/planning/capacity-exceptions", json={
            "work_centre_id": centre["id"], "exception_date": _iso(1),
            "available_minutes": minutes, "reason": "Revised",
        })
    rows = client.get("/planning/capacity-exceptions", params={
        "work_centre_id": centre["id"]}).json()
    assert len(rows) == 1
    assert Decimal(rows[0]["available_minutes"]) == Decimal("4800.0000")


def test_booking_derives_minutes_from_the_sewing_order(client):
    ctx = _base(client)
    centre = _centre(client)
    sew = client.post("/production/sewing-orders", json={
        "style_id": ctx["style_id"], "line": "Line 1", "planned_qty": 300,
    }).json()

    booking = client.post("/planning/capacity-bookings", json={
        "work_centre_id": centre["id"], "sewing_order_id": sew["id"],
        "start_date": _iso(0), "end_date": _iso(4),
    }).json()
    # Style SAM is 12.5 → 300 × 12.5 = 3750 minutes.
    assert Decimal(booking["minutes"]) == Decimal("3750.0000")


def test_cancelled_bookings_release_capacity(client):
    centre = _centre(client)
    booking = client.post("/planning/capacity-bookings", json={
        "work_centre_id": centre["id"], "minutes": "10000",
        "start_date": _iso(0), "end_date": _iso(6),
    }).json()
    client.post(f"/planning/capacity-bookings/{booking['id']}/cancel")

    board = client.get("/planning/capacity-board", params={
        "horizon_start": _iso(0), "horizon_end": _iso(6), "bucket_days": 7,
    }).json()
    assert Decimal(board["centres"][0]["buckets"][0]["loaded_minutes"]) == Decimal("0.0000")


# --------------------------------------------------------------------------- #
# Available to promise
# --------------------------------------------------------------------------- #
def test_atp_promises_when_material_and_capacity_allow(client, session):
    ctx = _base(client)
    _centre(client)
    from app.inventory.models import MovementType
    from app.inventory.service import post_movement

    post_movement(
        session, material_id=ctx["fabric"], movement_type=MovementType.receipt,
        quantity=Decimal("10000"), unit_cost=Decimal("4.00"),
        reference_type="opening", actor="test",
    )
    session.commit()

    r = client.post("/planning/atp", json={
        "style_id": ctx["style_id"], "quantity": 100, "wanted_date": _iso(30),
    }).json()
    assert r["can_promise"] is True
    assert r["limiting_factor"] == "none"
    # 100 × 12.5 SAM = 1250 minutes, comfortably inside one day of the line.
    assert Decimal(r["required_minutes"]) == Decimal("1250.0000")
    assert all(Decimal(m["shortfall_qty"]) == 0 for m in r["materials"])


def test_atp_reports_material_as_the_limiting_factor(client):
    ctx = _base(client, lead_time_days=45)
    _centre(client)
    # No stock at all, and a 45-day lead time against a 10-day request.
    r = client.post("/planning/atp", json={
        "style_id": ctx["style_id"], "quantity": 100, "wanted_date": _iso(10),
    }).json()
    assert r["can_promise"] is False
    assert r["limiting_factor"] == "material"
    line = r["materials"][0]
    assert Decimal(line["shortfall_qty"]) > 0
    assert line["lead_time_days"] == 45
    promise = date.fromisoformat(r["promise_date"])
    assert (promise - _today()).days == 45


def test_atp_reports_capacity_as_the_limiting_factor(client, session):
    ctx = _base(client)
    # A deliberately tiny line: 1 operator × 480 min × 100% = 480 min/day.
    _centre(client, code="SEW-SMALL", operators=1, efficiency="100")
    from app.inventory.models import MovementType
    from app.inventory.service import post_movement

    post_movement(
        session, material_id=ctx["fabric"], movement_type=MovementType.receipt,
        quantity=Decimal("100000"), unit_cost=Decimal("4.00"),
        reference_type="opening", actor="test",
    )
    session.commit()

    # 5000 pcs × 12.5 = 62,500 minutes ≈ 131 days on this line.
    r = client.post("/planning/atp", json={
        "style_id": ctx["style_id"], "quantity": 5000, "wanted_date": _iso(7),
    }).json()
    assert r["can_promise"] is False
    assert r["limiting_factor"] == "capacity"
    assert date.fromisoformat(r["capacity_ready_date"]) > _today() + timedelta(days=7)


def test_atp_rejects_a_non_positive_quantity(client):
    ctx = _base(client)
    r = client.post("/planning/atp", json={
        "style_id": ctx["style_id"], "quantity": 0, "wanted_date": _iso(10),
    })
    assert r.status_code == 422
