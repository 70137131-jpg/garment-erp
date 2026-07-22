from decimal import Decimal


def test_create_and_list_colour(client):
    r = client.post("/masters/colours", json={"code": "NAVY", "name": "Navy", "hex": "#001f3f"})
    assert r.status_code == 201, r.text
    assert r.json()["code"] == "NAVY"

    r = client.get("/masters/colours")
    assert [c["code"] for c in r.json()] == ["NAVY"]


def test_duplicate_colour_code_rejected(client):
    client.post("/masters/colours", json={"code": "NAVY", "name": "Navy"})
    r = client.post("/masters/colours", json={"code": "NAVY", "name": "Navy Blue"})
    assert r.status_code == 409


def test_customer_gets_auto_code(client):
    r = client.post("/masters/customers", json={"name": "ACME Apparel", "currency": "USD"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["code"].startswith("CUST-")

    r2 = client.post("/masters/customers", json={"name": "Globex", "currency": "EUR"})
    # Auto codes are sequential and distinct.
    assert r2.json()["code"] != body["code"]


def test_size_range_preserves_order(client):
    payload = {
        "code": "MENS-STD",
        "name": "Mens Standard",
        "sizes": [
            {"position": 1, "label": "S"},
            {"position": 2, "label": "M"},
            {"position": 3, "label": "L"},
            {"position": 4, "label": "XL"},
        ],
    }
    r = client.post("/masters/size-ranges", json=payload)
    assert r.status_code == 201, r.text
    assert [s["label"] for s in r.json()["sizes"]] == ["S", "M", "L", "XL"]


def test_supplier_material_types(client):
    r = client.post(
        "/masters/suppliers",
        json={"name": "Textile Mills Ltd", "material_types": ["fabric", "trims"]},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["code"].startswith("SUP-")
    assert set(body["material_types"]) == {"fabric", "trims"}


def test_fabric_material_dual_uom(client):
    r = client.post(
        "/masters/materials",
        json={
            "name": "Single Jersey 180gsm",
            "material_type": "fabric",
            "base_uom": "metre",
            "purchase_uom": "roll",
            "purchase_to_base_factor": "50",
            "lot_tracked": True,
            "composition": "100% Cotton",
            "gsm": "180",
            "width_cm": "150",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["code"].startswith("FAB-")
    assert Decimal(body["purchase_to_base_factor"]) == Decimal("50")
    assert body["lot_tracked"] is True


def test_material_code_prefix_by_type(client):
    r = client.post(
        "/masters/materials",
        json={"name": "Poly Thread 40s", "material_type": "thread"},
    )
    assert r.json()["code"].startswith("THR-")


def test_material_rejects_nonpositive_conversion(client):
    r = client.post(
        "/masters/materials",
        json={
            "name": "Bad Fabric",
            "material_type": "fabric",
            "purchase_to_base_factor": "0",
        },
    )
    assert r.status_code == 422


def test_material_filter_by_type(client):
    client.post("/masters/materials", json={"name": "Fab A", "material_type": "fabric"})
    client.post("/masters/materials", json={"name": "Trim B", "material_type": "trims"})
    r = client.get("/masters/materials", params={"material_type": "fabric"})
    names = [m["name"] for m in r.json()]
    assert names == ["Fab A"]
