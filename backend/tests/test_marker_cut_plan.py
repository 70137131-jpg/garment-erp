"""Module 14 — marker efficiency and lay planning.

The solver is a heuristic, not an exact optimiser, so the tests assert the
properties that actually matter operationally:

* the plan **covers** demand — never under-delivers;
* it does not overcut when an exact-fit combination exists;
* marker efficiency is **derived from geometry**, not accepted on trust;
* the ply-reduction pass genuinely removes fabric that greedy overshot.

Where an exact optimum is knowable by hand, it is asserted directly.
"""

from decimal import Decimal

from tests.factories import make_colour, make_customer, make_size_range, make_style


def _marker(client, style_id, code, sizes, length_cm, width_cm="150",
            pattern_area=None, max_plies=100, approve=True):
    """Create a marker; pattern area defaults to a realistic ~85% fill."""
    if pattern_area is None:
        pattern_area = str(Decimal(length_cm) * Decimal(width_cm) * Decimal("0.85"))
    body = client.post("/marker/markers", json={
        "marker_code": code, "style_id": style_id,
        "width_cm": width_cm, "length_cm": length_cm,
        "pattern_area_cm2": pattern_area, "max_plies": max_plies,
        "sizes": [{"size_label": s, "quantity": q} for s, q in sizes.items()],
    })
    assert body.status_code == 201, body.text
    marker = body.json()
    if approve:
        client.post(f"/marker/markers/{marker['id']}/approve")
    return marker


def _style(client):
    sr = make_size_range(client)
    return make_style(client, sr)


# --------------------------------------------------------------------------- #
# Marker geometry
# --------------------------------------------------------------------------- #
def test_marker_efficiency_is_derived_not_asserted(client):
    style_id = _style(client)
    # 1000 cm × 150 cm = 150,000 cm²; pattern area 127,500 cm² → 85%.
    marker = _marker(client, style_id, "MK-1", {"M": 2}, "1000", "150",
                     pattern_area="127500")
    assert Decimal(marker["efficiency_pct"]) == Decimal("85.000000")
    assert marker["pieces_per_ply"] == 2


def test_pattern_area_larger_than_the_rectangle_is_impossible(client):
    style_id = _style(client)
    r = client.post("/marker/markers", json={
        "marker_code": "MK-BAD", "style_id": style_id,
        "width_cm": "150", "length_cm": "100",
        "pattern_area_cm2": "20000",   # 15,000 cm² rectangle
        "sizes": [{"size_label": "M", "quantity": 1}],
    })
    assert r.status_code == 422
    assert "cannot fit" in r.json()["detail"]


def test_marker_needs_at_least_one_size(client):
    style_id = _style(client)
    r = client.post("/marker/markers", json={
        "marker_code": "MK-EMPTY", "style_id": style_id,
        "width_cm": "150", "length_cm": "100", "pattern_area_cm2": "1000",
        "sizes": [],
    })
    assert r.status_code == 422


def test_duplicate_marker_code_is_rejected(client):
    style_id = _style(client)
    _marker(client, style_id, "MK-1", {"M": 1}, "500")
    r = client.post("/marker/markers", json={
        "marker_code": "mk-1", "style_id": style_id,
        "width_cm": "150", "length_cm": "500", "pattern_area_cm2": "1000",
        "sizes": [{"size_label": "M", "quantity": 1}],
    })
    assert r.status_code == 422
    assert "already exists" in r.json()["detail"]


# --------------------------------------------------------------------------- #
# Lay planning
# --------------------------------------------------------------------------- #
def test_exact_fit_plan_has_no_overcut(client):
    """One marker of S+M+L+XL, 50 plies, covers a 50/50/50/50 order exactly."""
    style_id = _style(client)
    _marker(client, style_id, "MK-ALL", {"S": 1, "M": 1, "L": 1, "XL": 1}, "400")

    plan = client.post("/marker/cut-plans", json={
        "style_id": style_id, "width_cm": "150",
        "sizes": [{"size_label": s, "quantity": 50} for s in ("S", "M", "L", "XL")],
    }).json()

    assert plan["required_pieces"] == 200
    assert plan["planned_pieces"] == 200
    assert plan["overcut_pieces"] == 0
    assert plan["lay_count"] == 1
    assert plan["total_plies"] == 50
    # 400 cm × 50 plies = 20,000 cm = 200 m
    assert Decimal(plan["total_fabric_m"]) == Decimal("200.0000")
    assert Decimal(plan["weighted_efficiency_pct"]) == Decimal("85.000000")


def test_plan_always_covers_demand(client):
    """A ratio marker plus a single-size marker must still cover an odd order."""
    style_id = _style(client)
    _marker(client, style_id, "MK-RATIO", {"S": 1, "M": 2, "L": 1}, "400")
    _marker(client, style_id, "MK-M", {"M": 1}, "120")

    plan = client.post("/marker/cut-plans", json={
        "style_id": style_id, "width_cm": "150",
        "sizes": [
            {"size_label": "S", "quantity": 30},
            {"size_label": "M", "quantity": 95},
            {"size_label": "L", "quantity": 30},
        ],
    }).json()

    by_size = {d["size_label"]: d for d in plan["demands"]}
    for size, required in (("S", 30), ("M", 95), ("L", 30)):
        assert by_size[size]["planned_qty"] >= required, f"{size} under-delivered"
    assert plan["planned_pieces"] >= plan["required_pieces"]


def test_ply_reduction_beats_naive_greedy(client):
    """Greedy overshoots on its last lay; the reduction pass reclaims it.

    Demand 100/100. A 1×S+1×M marker at 400 cm covers it in exactly 100 plies
    (40,000 cm). A wasteful single-size marker exists too, and must not be used.
    """
    style_id = _style(client)
    _marker(client, style_id, "MK-PAIR", {"S": 1, "M": 1}, "400")
    _marker(client, style_id, "MK-SOLO", {"S": 1}, "390")

    plan = client.post("/marker/cut-plans", json={
        "style_id": style_id, "width_cm": "150",
        "sizes": [{"size_label": "S", "quantity": 100},
                  {"size_label": "M", "quantity": 100}],
    }).json()

    assert plan["overcut_pieces"] == 0
    assert Decimal(plan["total_fabric_m"]) == Decimal("400.0000")
    assert [l["marker_code"] for l in plan["lays"]] == ["MK-PAIR"]


def test_max_plies_forces_multiple_lays(client):
    """A spreading limit splits one conceptual lay into several."""
    style_id = _style(client)
    _marker(client, style_id, "MK-ALL", {"S": 1, "M": 1}, "400", max_plies=30)

    plan = client.post("/marker/cut-plans", json={
        "style_id": style_id, "width_cm": "150", "max_plies": 30,
        "sizes": [{"size_label": "S", "quantity": 100},
                  {"size_label": "M", "quantity": 100}],
    }).json()

    assert plan["total_plies"] == 100
    assert plan["lay_count"] >= 4          # 100 plies at 30 per lay
    assert all(l["plies"] <= 30 for l in plan["lays"])


def test_unreachable_size_is_reported_not_silently_dropped(client):
    style_id = _style(client)
    _marker(client, style_id, "MK-SM", {"S": 1, "M": 1}, "400")

    r = client.post("/marker/cut-plans", json={
        "style_id": style_id, "width_cm": "150",
        "sizes": [{"size_label": "S", "quantity": 10},
                  {"size_label": "XL", "quantity": 10}],
    })
    assert r.status_code == 422
    assert "XL" in r.json()["detail"]


def test_exact_fit_can_be_demanded(client):
    """allow_overcut=false refuses a plan that would over-produce."""
    style_id = _style(client)
    _marker(client, style_id, "MK-PAIR", {"S": 2, "M": 1}, "400")

    r = client.post("/marker/cut-plans", json={
        "style_id": style_id, "width_cm": "150", "allow_overcut": False,
        "sizes": [{"size_label": "S", "quantity": 10},
                  {"size_label": "M", "quantity": 10}],
    })
    assert r.status_code == 422
    assert "overcut" in r.json()["detail"].lower()


def test_width_must_match_an_approved_marker(client):
    style_id = _style(client)
    _marker(client, style_id, "MK-150", {"M": 1}, "400", width_cm="150")
    r = client.post("/marker/cut-plans", json={
        "style_id": style_id, "width_cm": "160",
        "sizes": [{"size_label": "M", "quantity": 10}],
    })
    assert r.status_code == 422
    assert "no approved markers" in r.json()["detail"].lower()


def test_draft_markers_are_not_used_for_planning(client):
    style_id = _style(client)
    _marker(client, style_id, "MK-DRAFT", {"M": 1}, "400", approve=False)
    r = client.post("/marker/cut-plans", json={
        "style_id": style_id, "width_cm": "150",
        "sizes": [{"size_label": "M", "quantity": 10}],
    })
    assert r.status_code == 422


def test_plan_can_take_demand_from_a_sales_order(client):
    style_id = _style(client)
    colour = make_colour(client)
    customer = make_customer(client)
    _marker(client, style_id, "MK-ALL", {"S": 1, "M": 2, "L": 1}, "500")

    order = client.post("/sales-orders", json={
        "customer_id": customer,
        "lines": [{"style_id": style_id, "colour_id": colour, "unit_price": "10.00",
                   "sizes": [{"size_label": "S", "ordered_qty": 40},
                             {"size_label": "M", "ordered_qty": 80},
                             {"size_label": "L", "ordered_qty": 40}]}],
    }).json()
    client.post(f"/sales-orders/{order['id']}/confirm")

    plan = client.post("/marker/cut-plans", json={
        "style_id": style_id, "width_cm": "150", "sales_order_id": order["id"],
    }).json()
    assert plan["required_pieces"] == 160
    assert plan["sales_order_id"] == order["id"]
    assert plan["overcut_pieces"] == 0


def test_approving_a_plan_stamps_the_cut_order_efficiency(client):
    """This is what makes CutOrder.marker_efficiency a derived number."""
    style_id = _style(client)
    colour = make_colour(client)
    fabric = client.post("/masters/materials", json={
        "name": "Jersey", "material_type": "fabric", "base_uom": "metre",
        "purchase_uom": "roll", "purchase_to_base_factor": "50", "width_cm": "150",
    }).json()["id"]
    from tests.factories import make_approved_bom
    make_approved_bom(client, style_id, fabric, wastage="0")
    # 400 cm × 150 cm = 60,000 cm² rectangle; 48,000 cm² of pattern → 80%.
    _marker(client, style_id, "MK-ALL", {"S": 1, "M": 1}, "400", pattern_area="48000")

    cut = client.post("/production/cut-orders", json={
        "style_id": style_id, "colour_id": colour,
        "sizes": [{"size_label": "S", "planned_qty": 50},
                  {"size_label": "M", "planned_qty": 50}],
    }).json()
    assert cut["marker_efficiency"] is None

    plan = client.post("/marker/cut-plans", json={
        "style_id": style_id, "width_cm": "150", "cut_order_id": cut["id"],
        "fabric_material_id": fabric,
        "sizes": [{"size_label": "S", "quantity": 50},
                  {"size_label": "M", "quantity": 50}],
    }).json()
    approved = client.post(f"/marker/cut-plans/{plan['id']}/approve").json()
    assert approved["status"] == "approved"

    # 120,000 / (400 × 150) = 80%
    assert Decimal(approved["weighted_efficiency_pct"]) == Decimal("80.000000")
    refreshed = client.get(f"/production/cut-orders/{cut['id']}").json()
    assert Decimal(refreshed["marker_efficiency"]) == Decimal("80.000000")


def test_plan_reports_its_saving_against_the_bom_estimate(client):
    """The marker plan is operational truth; the BOM figure is the benchmark."""
    style_id = _style(client)
    fabric = client.post("/masters/materials", json={
        "name": "Jersey", "material_type": "fabric", "base_uom": "metre",
        "purchase_uom": "roll", "purchase_to_base_factor": "50", "width_cm": "150",
    }).json()["id"]
    from tests.factories import make_approved_bom
    make_approved_bom(client, style_id, fabric, wastage="0")
    # 400 cm per ply yields 2 garments → 2.0 m/garment, vs BOM 1.0/1.2 m.
    _marker(client, style_id, "MK-ALL", {"S": 1, "M": 1}, "400")

    plan = client.post("/marker/cut-plans", json={
        "style_id": style_id, "width_cm": "150", "fabric_material_id": fabric,
        "sizes": [{"size_label": "S", "quantity": 50},
                  {"size_label": "M", "quantity": 50}],
    }).json()

    # BOM: 50×1.0 + 50×1.2 = 110 m. Marker plan: 400 cm × 50 = 200 m.
    assert Decimal(plan["bom_fabric_m"]) == Decimal("110.0000")
    assert Decimal(plan["total_fabric_m"]) == Decimal("200.0000")
    assert Decimal(plan["saving_vs_bom_m"]) == Decimal("-90.0000")


def test_cut_plan_export(client):
    style_id = _style(client)
    _marker(client, style_id, "MK-ALL", {"S": 1, "M": 1}, "400")
    plan = client.post("/marker/cut-plans", json={
        "style_id": style_id, "width_cm": "150",
        "sizes": [{"size_label": "S", "quantity": 10},
                  {"size_label": "M", "quantity": 10}],
    }).json()
    csv = client.get(f"/marker/cut-plans/{plan['id']}/export")
    assert csv.status_code == 200
    assert "Marker efficiency %" in csv.text
