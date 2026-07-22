from decimal import Decimal

import pytest

from app.styles.models import BomVersion
from app.styles.service import current_bom_version, material_requirements


def _make_size_range(client):
    r = client.post(
        "/masters/size-ranges",
        json={
            "code": "MENS-STD",
            "name": "Mens Standard",
            "sizes": [
                {"position": 1, "label": "S"},
                {"position": 2, "label": "M"},
                {"position": 3, "label": "L"},
                {"position": 4, "label": "XL"},
            ],
        },
    )
    return r.json()["id"]


def _make_fabric(client, name="Single Jersey"):
    r = client.post(
        "/masters/materials",
        json={
            "name": name,
            "material_type": "fabric",
            "base_uom": "metre",
            "purchase_uom": "roll",
            "purchase_to_base_factor": "50",
            "lot_tracked": True,
        },
    )
    return r.json()["id"]


def _make_style(client, size_range_id, number="TS-100"):
    r = client.post(
        "/styles",
        json={
            "style_number": number,
            "description": "Crew Neck Tee",
            "size_range_id": size_range_id,
            "gender": "mens",
            "standard_sam": "12.5",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_style_and_colourway_flow(client):
    sr = _make_size_range(client)
    style_id = _make_style(client, sr)

    colour = client.post("/masters/colours", json={"code": "NAVY", "name": "Navy"}).json()
    r = client.post(
        f"/styles/{style_id}/colourways",
        json={"colour_id": colour["id"], "buyer_reference": "BR-NAVY"},
    )
    assert r.status_code == 201, r.text
    cw = r.json()
    assert cw["lab_dip_approved"] is False

    r = client.post(f"/styles/colourways/{cw['id']}/approve-lab-dip")
    assert r.status_code == 200
    assert r.json()["lab_dip_approved"] is True


def test_bom_rejects_size_outside_range(client):
    sr = _make_size_range(client)
    style_id = _make_style(client, sr)
    fabric = _make_fabric(client)

    r = client.post(
        f"/styles/{style_id}/bom-versions",
        json={
            "lines": [
                {
                    "material_id": fabric,
                    "wastage_pct": "0.05",
                    "size_consumption": [{"size_label": "XXL", "consumption": "1.5"}],
                }
            ]
        },
    )
    assert r.status_code == 422
    assert "size range" in r.json()["detail"].lower()


def test_bom_versioning_and_supersession(client):
    sr = _make_size_range(client)
    style_id = _make_style(client, sr)
    fabric = _make_fabric(client)

    def make_version():
        return client.post(
            f"/styles/{style_id}/bom-versions",
            json={
                "lines": [
                    {
                        "material_id": fabric,
                        "wastage_pct": "0.05",
                        "size_consumption": [
                            {"size_label": "S", "consumption": "1.2"},
                            {"size_label": "M", "consumption": "1.3"},
                            {"size_label": "L", "consumption": "1.4"},
                            {"size_label": "XL", "consumption": "1.5"},
                        ],
                    }
                ]
            },
        ).json()

    v1 = make_version()
    assert v1["version_no"] == 1
    assert v1["status"] == "draft"

    r = client.post(f"/styles/bom-versions/{v1['id']}/approve")
    assert r.status_code == 200
    assert r.json()["status"] == "approved"

    v2 = make_version()
    assert v2["version_no"] == 2
    r = client.post(f"/styles/bom-versions/{v2['id']}/approve")
    assert r.status_code == 200

    # Approving v2 must supersede v1 — history preserved, single live recipe.
    v1_now = client.get(f"/styles/bom-versions/{v1['id']}").json()
    assert v1_now["status"] == "superseded"

    # Re-approving a superseded version is rejected.
    r = client.post(f"/styles/bom-versions/{v1['id']}/approve")
    assert r.status_code == 409


def test_duplicate_material_in_bom_rejected(client):
    sr = _make_size_range(client)
    style_id = _make_style(client, sr)
    fabric = _make_fabric(client)
    r = client.post(
        f"/styles/{style_id}/bom-versions",
        json={
            "lines": [
                {"material_id": fabric, "size_consumption": []},
                {"material_id": fabric, "size_consumption": []},
            ]
        },
    )
    assert r.status_code == 422


def test_material_requirements_calc(client, session):
    sr = _make_size_range(client)
    style_id = _make_style(client, sr)
    fabric = _make_fabric(client)
    client.post(
        f"/styles/{style_id}/bom-versions",
        json={
            "lines": [
                {
                    "material_id": fabric,
                    "wastage_pct": "0.05",
                    "size_consumption": [
                        {"size_label": "S", "consumption": "1.0"},
                        {"size_label": "M", "consumption": "1.2"},
                        {"size_label": "L", "consumption": "1.4"},
                        {"size_label": "XL", "consumption": "1.6"},
                    ],
                }
            ]
        },
    )
    version = current_bom_version(session, style_id) or session.get(BomVersion, 1)

    # 10 S + 20 M + 0 L + 5 XL = 10*1.0 + 20*1.2 + 5*1.6 = 42.0, +5% wastage = 44.1
    reqs = material_requirements(session, version, {"S": 10, "M": 20, "XL": 5})
    assert reqs[fabric] == Decimal("44.1000")
