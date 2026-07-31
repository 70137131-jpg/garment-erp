"""Module 12 — MES shift execution and OEE.

The worked example below is deliberately arithmetic-heavy. OEE is a number
people set targets and bonuses against, so each factor is pinned to an exact
expected value rather than a range.
"""

from datetime import date, timedelta
from decimal import Decimal


def _centre(client, code="SEW-1", operators=25):
    return client.post("/planning/work-centres", json={
        "code": code, "name": "Sewing line 1", "centre_type": "sewing",
        "operators": operators, "shift_minutes": 480, "shifts_per_day": 1,
        "efficiency_pct": "100",
    }).json()


def _reason(client, code, planned, category="breakdown"):
    return client.post("/mes/downtime-reasons", json={
        "code": code, "description": f"{code} reason",
        "category": category, "planned": planned,
    }).json()


def _log(client, centre_id, planned_minutes="480", ideal_cycle_seconds="42", day=None):
    return client.post("/mes/shift-logs", json={
        "work_centre_id": centre_id,
        "log_date": (day or date.today()).isoformat(),
        "shift": "A", "operators": 25,
        "planned_minutes": planned_minutes,
        "ideal_cycle_seconds": ideal_cycle_seconds,
    }).json()


def test_oee_worked_example(client):
    """480 planned − 30 planned stop − 60 breakdown, 500 made, 480 good."""
    centre = _centre(client)
    changeover = _reason(client, "CHG", True, "changeover")
    breakdown = _reason(client, "BRK", False, "breakdown")
    log = _log(client, centre["id"])

    client.post(f"/mes/shift-logs/{log['id']}/downtime",
                json={"reason_id": changeover["id"], "minutes": "30"})
    client.post(f"/mes/shift-logs/{log['id']}/downtime",
                json={"reason_id": breakdown["id"], "minutes": "60"})
    client.patch(f"/mes/shift-logs/{log['id']}/counts",
                 json={"total_count": 500, "good_count": 480, "reject_count": 20})

    closed = client.post(f"/mes/shift-logs/{log['id']}/close").json()
    assert closed["status"] == "closed"

    # Planned production time = 480 − 30 = 450; run time = 450 − 60 = 390.
    assert Decimal(closed["planned_downtime_minutes"]) == Decimal("30.0000")
    assert Decimal(closed["unplanned_downtime_minutes"]) == Decimal("60.0000")
    assert Decimal(closed["run_minutes"]) == Decimal("390.0000")

    # Availability 390/450, performance (42s × 500 = 350 min)/390, quality 480/500.
    assert Decimal(closed["availability_pct"]) == Decimal("86.666667")
    assert Decimal(closed["performance_pct"]) == Decimal("89.743590")
    assert Decimal(closed["quality_pct"]) == Decimal("96.000000")
    # 350/450 × 0.96 = 74.666667%
    assert Decimal(closed["oee_pct"]) == Decimal("74.666667")
    assert closed["performance_capped"] is False


def test_planned_downtime_leaves_the_denominator_not_the_score(client):
    """A maintained line must not score worse than a neglected one.

    Identical shifts, identical stoppage length — one planned, one not. The
    planned stop leaves availability at 100%; the unplanned one does not.
    """
    centre_a = _centre(client, "A")
    centre_b = _centre(client, "B")
    planned = _reason(client, "PM", True, "planned_maintenance")
    unplanned = _reason(client, "FAIL", False, "breakdown")

    for centre, reason in ((centre_a, planned), (centre_b, unplanned)):
        log = _log(client, centre["id"])
        client.post(f"/mes/shift-logs/{log['id']}/downtime",
                    json={"reason_id": reason["id"], "minutes": "60"})
        client.patch(f"/mes/shift-logs/{log['id']}/counts",
                     json={"total_count": 100, "good_count": 100, "reject_count": 0})
        closed = client.post(f"/mes/shift-logs/{log['id']}/close").json()
        if reason is planned:
            assert Decimal(closed["availability_pct"]) == Decimal("100.000000")
        else:
            # 420 run / 480 planned production time
            assert Decimal(closed["availability_pct"]) == Decimal("87.500000")


def test_performance_above_100_is_capped_and_flagged(client):
    """Beating the rated cycle means the master data is wrong, not the line."""
    centre = _centre(client)
    # 120 s/piece rated, but 500 pieces in 480 minutes = 57.6 s/piece actual.
    log = _log(client, centre["id"], ideal_cycle_seconds="120")
    client.patch(f"/mes/shift-logs/{log['id']}/counts",
                 json={"total_count": 500, "good_count": 500, "reject_count": 0})
    closed = client.post(f"/mes/shift-logs/{log['id']}/close").json()

    assert Decimal(closed["performance_pct"]) == Decimal("100.000000")
    assert closed["performance_capped"] is True
    # OEE cannot exceed availability × quality once performance is capped.
    assert Decimal(closed["oee_pct"]) == Decimal("100.000000")


def test_counts_must_reconcile_before_close(client):
    centre = _centre(client)
    log = _log(client, centre["id"])
    client.patch(f"/mes/shift-logs/{log['id']}/counts",
                 json={"total_count": 100, "good_count": 80, "reject_count": 5})
    r = client.post(f"/mes/shift-logs/{log['id']}/close")
    assert r.status_code == 422
    assert "sum to the total" in r.json()["detail"]


def test_downtime_cannot_exceed_the_shift(client):
    centre = _centre(client)
    reason = _reason(client, "BRK", False)
    log = _log(client, centre["id"], planned_minutes="480")
    client.post(f"/mes/shift-logs/{log['id']}/downtime",
                json={"reason_id": reason["id"], "minutes": "400"})
    r = client.post(f"/mes/shift-logs/{log['id']}/downtime",
                    json={"reason_id": reason["id"], "minutes": "200"})
    assert r.status_code == 422
    assert "exceed" in r.json()["detail"].lower()


def test_zero_planned_production_time_reports_zero_not_an_error(client):
    """A shift entirely consumed by planned downtime has no denominator."""
    centre = _centre(client)
    planned = _reason(client, "PM", True, "planned_maintenance")
    log = _log(client, centre["id"], planned_minutes="480")
    client.post(f"/mes/shift-logs/{log['id']}/downtime",
                json={"reason_id": planned["id"], "minutes": "480"})
    closed = client.post(f"/mes/shift-logs/{log['id']}/close").json()
    assert Decimal(closed["availability_pct"]) == Decimal("0.000000")
    assert Decimal(closed["oee_pct"]) == Decimal("0.000000")


def test_closed_logs_are_frozen(client):
    centre = _centre(client)
    log = _log(client, centre["id"])
    client.patch(f"/mes/shift-logs/{log['id']}/counts",
                 json={"total_count": 10, "good_count": 10, "reject_count": 0})
    client.post(f"/mes/shift-logs/{log['id']}/close")

    assert client.post(f"/mes/shift-logs/{log['id']}/close").status_code == 422
    assert client.patch(f"/mes/shift-logs/{log['id']}/counts",
                        json={"total_count": 999}).status_code == 409
    reason = _reason(client, "BRK", False)
    assert client.post(f"/mes/shift-logs/{log['id']}/downtime",
                       json={"reason_id": reason["id"], "minutes": "5"}).status_code == 422


def test_duplicate_shift_log_is_rejected(client):
    centre = _centre(client)
    _log(client, centre["id"])
    r = client.post("/mes/shift-logs", json={
        "work_centre_id": centre["id"], "log_date": date.today().isoformat(),
        "shift": "A", "planned_minutes": "480",
    })
    assert r.status_code == 422
    assert "already exists" in r.json()["detail"]


def test_open_log_shows_a_live_preview_without_freezing_it(client):
    centre = _centre(client)
    log = _log(client, centre["id"])
    client.patch(f"/mes/shift-logs/{log['id']}/counts",
                 json={"total_count": 400, "good_count": 400, "reject_count": 0})
    live = client.get(f"/mes/shift-logs/{log['id']}").json()

    assert live["status"] == "open"
    assert Decimal(live["oee_pct"]) > 0          # previewed
    assert Decimal(live["run_minutes"]) == Decimal("480.0000")
    # …but nothing was frozen: the log is still open and unclosed.
    assert live["closed_at"] is None
    assert live["closed_by"] is None


def test_oee_summary_weights_by_minutes_not_by_shift(client):
    """Averaging percentages would let a short shift outvote a long one."""
    centre = _centre(client)
    today = date.today()

    # Long shift at 100% quality, short shift at 0% — weighted result must sit
    # far closer to the long shift than a naive 50/50 average would.
    long_log = _log(client, centre["id"], planned_minutes="960", day=today)
    client.patch(f"/mes/shift-logs/{long_log['id']}/counts",
                 json={"total_count": 1000, "good_count": 1000, "reject_count": 0})
    client.post(f"/mes/shift-logs/{long_log['id']}/close")

    short = client.post("/mes/shift-logs", json={
        "work_centre_id": centre["id"], "log_date": today.isoformat(),
        "shift": "B", "planned_minutes": "60", "ideal_cycle_seconds": "42",
    }).json()
    client.patch(f"/mes/shift-logs/{short['id']}/counts",
                 json={"total_count": 100, "good_count": 0, "reject_count": 100})
    client.post(f"/mes/shift-logs/{short['id']}/close")

    summary = client.get("/mes/oee", params={
        "date_from": today.isoformat(), "date_to": today.isoformat(),
    }).json()
    assert summary["shifts"] == 2
    assert summary["total_count"] == 1100
    assert summary["good_count"] == 1000
    # 1000/1100 = 90.909091%, not the 50% a naive average of 100% and 0% gives.
    assert Decimal(summary["quality_pct"]) == Decimal("90.909091")


def test_downtime_pareto_ranks_and_accumulates(client):
    centre = _centre(client)
    big = _reason(client, "BIG", False, "breakdown")
    small = _reason(client, "SMALL", False, "material_shortage")
    log = _log(client, centre["id"])
    client.post(f"/mes/shift-logs/{log['id']}/downtime",
                json={"reason_id": big["id"], "minutes": "90"})
    client.post(f"/mes/shift-logs/{log['id']}/downtime",
                json={"reason_id": small["id"], "minutes": "30"})
    client.patch(f"/mes/shift-logs/{log['id']}/counts",
                 json={"total_count": 10, "good_count": 10, "reject_count": 0})
    client.post(f"/mes/shift-logs/{log['id']}/close")

    today = date.today().isoformat()
    pareto = client.get("/mes/oee", params={"date_from": today, "date_to": today}).json()["pareto"]
    assert [p["reason_code"] for p in pareto] == ["BIG", "SMALL"]
    assert Decimal(pareto[0]["share_pct"]) == Decimal("75.000000")
    assert Decimal(pareto[-1]["cumulative_pct"]) == Decimal("100.000000")


def test_andon_board_reports_line_state(client):
    centre = _centre(client)
    board = client.get("/mes/andon").json()
    assert board["lines"][0]["status"] == "idle"

    log = _log(client, centre["id"])
    client.patch(f"/mes/shift-logs/{log['id']}/counts", json={"total_count": 50})
    board = client.get("/mes/andon").json()
    assert board["lines"][0]["status"] == "running"
    assert board["lines"][0]["open_logs"] == 1


def test_machine_must_belong_to_its_work_centre(client):
    a = _centre(client, "A")
    b = _centre(client, "B")
    machine = client.post("/mes/machines", json={
        "code": "M1", "name": "Overlock", "work_centre_id": a["id"],
        "ideal_cycle_seconds": "42",
    }).json()
    r = client.post("/mes/shift-logs", json={
        "work_centre_id": b["id"], "machine_id": machine["id"],
        "log_date": date.today().isoformat(), "planned_minutes": "480",
    })
    assert r.status_code == 422
    assert "does not belong" in r.json()["detail"]


def test_oee_export(client):
    centre = _centre(client)
    log = _log(client, centre["id"])
    client.patch(f"/mes/shift-logs/{log['id']}/counts",
                 json={"total_count": 100, "good_count": 100, "reject_count": 0})
    client.post(f"/mes/shift-logs/{log['id']}/close")
    csv = client.get("/mes/oee/export")
    assert csv.status_code == 200
    assert "OEE %" in csv.text
