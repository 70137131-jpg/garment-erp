"""Module 13 — mobile WMS: bins, directed tasks, scanning.

Two behaviours carry the weight here:

* completing a task moves stock **through the ledger**, so a task and its
  movement can never disagree about what happened;
* completion is **idempotent**, because a handheld on bad Wi-Fi will retry and
  a second movement would be a real inventory error.
"""

from decimal import Decimal

from app.inventory.models import MovementType
from app.inventory.service import balance, post_movement
from tests.factories import make_fabric


def _bin(client, code, sequence=0, warehouse="MAIN", bin_type="storage"):
    return client.post("/wms/bins", json={
        "code": code, "warehouse": warehouse, "zone": "Z1",
        "bin_type": bin_type, "pick_sequence": sequence,
    }).json()


def _stocked_material(client, session, qty="500", location="A-01-01"):
    material = make_fabric(client)
    post_movement(
        session, material_id=material, movement_type=MovementType.receipt,
        quantity=Decimal(qty), unit_cost=Decimal("4.00"),
        location=location, reference_type="opening", actor="test",
    )
    session.commit()
    return material


def test_bin_codes_are_unique_per_warehouse(client):
    _bin(client, "A-01-01")
    assert client.post("/wms/bins", json={
        "code": "A-01-01", "warehouse": "MAIN"}).status_code == 409
    # The same code in a different warehouse is a different place.
    assert client.post("/wms/bins", json={
        "code": "A-01-01", "warehouse": "OVERFLOW"}).status_code == 201


def test_moving_tasks_require_both_bins(client, session):
    material = _stocked_material(client, session)
    source = _bin(client, "A-01-01")
    r = client.post("/wms/tasks", json={
        "task_type": "pick", "material_id": material,
        "quantity": "10", "from_bin_id": source["id"],
    })
    assert r.status_code == 422
    assert "destination bin" in r.json()["detail"]


def test_completing_a_task_moves_stock_through_the_ledger(client, session):
    material = _stocked_material(client, session, qty="500", location="A-01-01")
    source = _bin(client, "A-01-01", sequence=1)
    destination = _bin(client, "B-02-01", sequence=2)

    task = client.post("/wms/tasks", json={
        "task_type": "transfer", "material_id": material, "quantity": "120",
        "from_bin_id": source["id"], "to_bin_id": destination["id"],
    }).json()
    assert task["status"] == "open"
    assert task["task_number"].startswith("TASK-")

    done = client.post(f"/wms/tasks/{task['id']}/complete", json={}).json()
    assert done["status"] == "completed"
    assert Decimal(done["completed_qty"]) == Decimal("120.0000")
    # The task points at the warehouse operation it produced.
    assert done["warehouse_operation_id"] is not None

    # And the ledger agrees: 120 left A-01-01 and arrived in B-02-01.
    assert balance(session, material_id=material, location="A-01-01") == Decimal("380.0000")
    assert balance(session, material_id=material, location="B-02-01") == Decimal("120.0000")
    # Total on hand is unchanged — a transfer creates no stock.
    assert balance(session, material_id=material) == Decimal("500.0000")


def test_completion_is_idempotent_for_handheld_retries(client, session):
    material = _stocked_material(client, session, qty="500", location="A-01-01")
    source = _bin(client, "A-01-01")
    destination = _bin(client, "B-02-01")
    task = client.post("/wms/tasks", json={
        "task_type": "transfer", "material_id": material, "quantity": "100",
        "from_bin_id": source["id"], "to_bin_id": destination["id"],
    }).json()

    first = client.post(f"/wms/tasks/{task['id']}/complete",
                        json={"client_key": "handheld-42"}).json()
    replay = client.post(f"/wms/tasks/{task['id']}/complete",
                         json={"client_key": "handheld-42"})
    assert replay.status_code == 200
    assert replay.json()["warehouse_operation_id"] == first["warehouse_operation_id"]

    # The retry moved nothing a second time.
    assert balance(session, material_id=material, location="B-02-01") == Decimal("100.0000")


def test_completing_twice_without_a_key_is_refused(client, session):
    material = _stocked_material(client, session, qty="500", location="A-01-01")
    source = _bin(client, "A-01-01")
    destination = _bin(client, "B-02-01")
    task = client.post("/wms/tasks", json={
        "task_type": "transfer", "material_id": material, "quantity": "100",
        "from_bin_id": source["id"], "to_bin_id": destination["id"],
    }).json()
    client.post(f"/wms/tasks/{task['id']}/complete", json={})
    again = client.post(f"/wms/tasks/{task['id']}/complete", json={})
    assert again.status_code == 422
    assert "already been completed" in again.json()["detail"]


def test_short_pick_needs_a_reason_and_is_recorded(client, session):
    material = _stocked_material(client, session, qty="500", location="A-01-01")
    source = _bin(client, "A-01-01")
    destination = _bin(client, "B-02-01")
    task = client.post("/wms/tasks", json={
        "task_type": "pick", "material_id": material, "quantity": "200",
        "from_bin_id": source["id"], "to_bin_id": destination["id"],
    }).json()

    refused = client.post(f"/wms/tasks/{task['id']}/complete",
                          json={"completed_qty": "150"})
    assert refused.status_code == 422
    assert "reason" in refused.json()["detail"].lower()

    done = client.post(f"/wms/tasks/{task['id']}/complete", json={
        "completed_qty": "150", "short_reason": "Only 150 m found in bin",
    }).json()
    assert Decimal(done["completed_qty"]) == Decimal("150.0000")
    assert done["short_reason"] == "Only 150 m found in bin"
    assert balance(session, material_id=material, location="B-02-01") == Decimal("150.0000")


def test_cannot_complete_more_than_directed(client, session):
    material = _stocked_material(client, session, qty="500", location="A-01-01")
    source = _bin(client, "A-01-01")
    destination = _bin(client, "B-02-01")
    task = client.post("/wms/tasks", json={
        "task_type": "pick", "material_id": material, "quantity": "100",
        "from_bin_id": source["id"], "to_bin_id": destination["id"],
    }).json()
    r = client.post(f"/wms/tasks/{task['id']}/complete", json={"completed_qty": "150"})
    assert r.status_code == 422
    assert "exceed" in r.json()["detail"].lower()


def test_task_list_is_ordered_as_a_walking_route(client, session):
    material = _stocked_material(client, session, qty="500", location="A-01-01")
    far = _bin(client, "Z-99-01", sequence=99)
    near = _bin(client, "A-01-01", sequence=1)
    destination = _bin(client, "OUT", sequence=100, bin_type="shipping")

    # Created far-first, so insertion order and route order disagree.
    for source in (far, near):
        client.post("/wms/tasks", json={
            "task_type": "pick", "material_id": material, "quantity": "10",
            "from_bin_id": source["id"], "to_bin_id": destination["id"],
        })

    tasks = client.get("/wms/tasks", params={"status": "open"}).json()
    assert [t["from_bin_code"] for t in tasks] == ["A-01-01", "Z-99-01"]


def test_cancelled_tasks_cannot_be_completed(client, session):
    material = _stocked_material(client, session)
    source = _bin(client, "A-01-01")
    destination = _bin(client, "B-02-01")
    task = client.post("/wms/tasks", json={
        "task_type": "transfer", "material_id": material, "quantity": "10",
        "from_bin_id": source["id"], "to_bin_id": destination["id"],
    }).json()
    client.post(f"/wms/tasks/{task['id']}/cancel")
    r = client.post(f"/wms/tasks/{task['id']}/complete", json={})
    assert r.status_code == 422
    assert "cancelled" in r.json()["detail"].lower()


def test_assigning_a_task_moves_it_to_a_queue(client, session):
    material = _stocked_material(client, session)
    source = _bin(client, "A-01-01")
    destination = _bin(client, "B-02-01")
    task = client.post("/wms/tasks", json={
        "task_type": "transfer", "material_id": material, "quantity": "10",
        "from_bin_id": source["id"], "to_bin_id": destination["id"],
    }).json()
    assigned = client.post(f"/wms/tasks/{task['id']}/assign",
                           json={"assigned_to": "picker@factory.local"}).json()
    assert assigned["status"] == "assigned"

    queue = client.get("/wms/tasks", params={"assigned_to": "picker@factory.local"}).json()
    assert [t["id"] for t in queue] == [task["id"]]


# --------------------------------------------------------------------------- #
# Scanning
# --------------------------------------------------------------------------- #
def test_scan_resolves_a_bin_and_its_open_work(client, session):
    material = _stocked_material(client, session)
    source = _bin(client, "A-01-01")
    destination = _bin(client, "B-02-01")
    client.post("/wms/tasks", json={
        "task_type": "pick", "material_id": material, "quantity": "10",
        "from_bin_id": source["id"], "to_bin_id": destination["id"],
    })

    r = client.post("/wms/scan", json={"code": "a-01-01"}).json()
    assert r["kind"] == "bin"
    assert r["code"] == "A-01-01"          # case-insensitive scan
    assert len(r["open_tasks"]) == 1


def test_scan_resolves_a_material(client, session):
    material = _stocked_material(client, session, qty="250")
    code = client.get(f"/masters/materials/{material}").json()["code"]
    r = client.post("/wms/scan", json={"code": code}).json()
    assert r["kind"] == "material"
    assert r["material_id"] == material
    assert Decimal(r["quantity"]) == Decimal("250.0000")


def test_scan_of_an_unknown_code_is_not_an_error(client):
    """The handheld needs a usable answer, not a 500."""
    r = client.post("/wms/scan", json={"code": "NOT-A-THING"})
    assert r.status_code == 200
    assert r.json()["kind"] == "unknown"


def test_empty_scan_is_rejected(client):
    r = client.post("/wms/scan", json={"code": "   "})
    assert r.status_code == 422
