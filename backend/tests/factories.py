"""Shared builders for tests — create the masters/styles a scenario needs."""

from decimal import Decimal


def make_size_range(client, code="MENS-STD"):
    return client.post(
        "/masters/size-ranges",
        json={
            "code": code,
            "name": "Mens Standard",
            "sizes": [
                {"position": 1, "label": "S"},
                {"position": 2, "label": "M"},
                {"position": 3, "label": "L"},
                {"position": 4, "label": "XL"},
            ],
        },
    ).json()["id"]


def make_colour(client, code="NAVY", name="Navy"):
    return client.post("/masters/colours", json={"code": code, "name": name}).json()["id"]


def make_customer(client, name="ACME Apparel", currency="USD"):
    return client.post(
        "/masters/customers", json={"name": name, "currency": currency}
    ).json()["id"]


def make_supplier(client, name="Textile Mills Ltd"):
    return client.post(
        "/masters/suppliers",
        json={"name": name, "material_types": ["fabric"]},
    ).json()["id"]


def make_fabric(client, name="Single Jersey", factor="50"):
    return client.post(
        "/masters/materials",
        json={
            "name": name,
            "material_type": "fabric",
            "base_uom": "metre",
            "purchase_uom": "roll",
            "purchase_to_base_factor": factor,
            "lot_tracked": True,
            "width_cm": "150",
        },
    ).json()["id"]


def make_style(client, size_range_id, number="TS-100", sam="12.5"):
    return client.post(
        "/styles",
        json={
            "style_number": number,
            "description": "Crew Neck Tee",
            "size_range_id": size_range_id,
            "gender": "mens",
            "standard_sam": sam,
        },
    ).json()["id"]


def make_approved_bom(client, style_id, material_id, wastage="0.05"):
    """Create and approve a single-fabric BOM version; return its id."""
    version = client.post(
        f"/styles/{style_id}/bom-versions",
        json={
            "lines": [
                {
                    "material_id": material_id,
                    "wastage_pct": wastage,
                    "size_consumption": [
                        {"size_label": "S", "consumption": "1.0"},
                        {"size_label": "M", "consumption": "1.2"},
                        {"size_label": "L", "consumption": "1.4"},
                        {"size_label": "XL", "consumption": "1.6"},
                    ],
                }
            ]
        },
    ).json()
    client.post(f"/styles/bom-versions/{version['id']}/approve")
    return version["id"]
