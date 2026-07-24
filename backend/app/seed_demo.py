"""Seed a realistic demo factory for sales demos and evaluation.

Run against an EMPTY development database:

    python -m app.seed_demo --password "Demo-Pass-2026!"

Creates login users for every role (…@demo.local), masters, styles with
approved BOMs and cost sheets, and three sales orders at different stages:
one fully shipped and settled (populates costing + P&L), one mid-production,
and one draft. All data is created through the real HTTP API in-process, so
every document has passed the same business rules production traffic does.

Refuses to run on a production environment or a non-empty database.
"""

import argparse
import secrets
import sys

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from .config import settings
from .db import engine
from .kernel.rbac import Principal, Role, get_current_principal
from .security.models import User, UserRole
from .security.service import hash_password

DEMO_DOMAIN = "demo.local"

DEMO_USERS = [
    ("demo.admin", Role.admin),
    ("meera.merchandiser", Role.merchandiser),
    ("paulo.procurement", Role.procurement),
    ("sana.stores", Role.stores),
    ("carlos.cutting", Role.cutting_supervisor),
    ("selin.sewing", Role.sewing_supervisor),
    ("quinn.quality", Role.quality_inspector),
    ("farah.finance", Role.finance),
    ("pat.planner", Role.planner),
]


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def ok(client_response, context: str):
    if client_response.status_code >= 400:
        fail(f"{context}: HTTP {client_response.status_code} — {client_response.text}")
    return client_response.json() if client_response.content else None


def create_users(session: Session, password: str) -> User:
    admin = None
    for handle, role in DEMO_USERS:
        user = User(
            email=f"{handle}@{DEMO_DOMAIN}",
            display_name=handle.replace(".", " ").title(),
            password_hash=hash_password(password),
            must_change_password=False,
        )
        session.add(user)
        session.flush()
        session.add(UserRole(user_id=user.id, role=role.value, granted_by=user.id))
        if role is Role.admin:
            admin = user
    session.commit()
    return admin


def seed(password: str) -> None:
    if settings.environment == "production":
        fail("Refusing to seed demo data into a production environment.")

    from .main import app

    with TestClient(app) as client:  # runs the lifespan: schema, CoA, subscribers
        with Session(engine) as session:
            if session.exec(select(User.id)).first() is not None:
                fail("Database already has users; seed only into a fresh database.")
            admin = create_users(session, password)
            principal = Principal(
                user_id=admin.id,
                email=admin.email,
                display_name=admin.display_name,
                roles=frozenset({Role.admin}),
                session_id=0,
            )
        app.dependency_overrides[get_current_principal] = lambda: principal
        try:
            client.get("/auth/csrf")
            client.headers["X-CSRF-Token"] = client.cookies.get(settings.csrf_cookie_name, "")
            build_factory(client)
        finally:
            app.dependency_overrides.pop(get_current_principal, None)


def build_factory(c: TestClient) -> None:
    # ------------------------------------------------------------- masters
    sr = ok(c.post("/masters/size-ranges", json={
        "code": "MENS-STD", "name": "Mens Standard",
        "sizes": [{"position": 1, "label": "S"}, {"position": 2, "label": "M"},
                  {"position": 3, "label": "L"}, {"position": 4, "label": "XL"}],
    }), "size range")["id"]
    colours = {}
    for code, name, hex_ in [("NAVY", "Navy", "#1F2A44"), ("WHITE", "Optic White", "#FAFAFA"),
                             ("BLACK", "Jet Black", "#111111"), ("OLIVE", "Olive", "#556B2F")]:
        colours[code] = ok(c.post("/masters/colours", json={"code": code, "name": name, "hex": hex_}), f"colour {code}")["id"]
    customers = {
        name: ok(c.post("/masters/customers", json={
            "name": name, "currency": currency, "payment_terms": terms, "credit_limit": limit,
        }), f"customer {name}")["id"]
        for name, currency, terms, limit in [
            ("Northwind Outfitters", "USD", "NET 30", "250000"),
            ("Atlantic Basics GmbH", "EUR", "NET 45", "150000"),
            ("Sierra Sportswear", "USD", "NET 30", "90000"),
        ]
    }
    suppliers = {
        name: ok(c.post("/masters/suppliers", json={
            "name": name, "material_types": types, "lead_time_days": lead,
        }), f"supplier {name}")["id"]
        for name, types, lead in [
            ("Textile Mills Ltd", ["fabric"], 21),
            ("TrimCo Accessories", ["trims", "labels", "packaging"], 10),
            ("ThreadWorks", ["thread"], 7),
        ]
    }
    fabric = ok(c.post("/masters/materials", json={
        "name": "Single Jersey 180gsm", "material_type": "fabric", "base_uom": "metre",
        "purchase_uom": "roll", "purchase_to_base_factor": "50",
        "lot_tracked": True, "width_cm": "150", "gsm": "180", "composition": "100% Cotton",
    }), "fabric")["id"]
    ok(c.post("/masters/materials", json={
        "name": "Pique 220gsm", "material_type": "fabric", "base_uom": "metre",
        "purchase_uom": "roll", "purchase_to_base_factor": "40",
        "lot_tracked": True, "width_cm": "160", "gsm": "220", "composition": "95% Cotton 5% Elastane",
    }), "fabric 2")
    trims = {
        name: ok(c.post("/masters/materials", json={
            "name": name, "material_type": mtype, "base_uom": uom, "purchase_uom": uom,
        }), f"material {name}")["id"]
        for name, mtype, uom in [
            ("Main Label Woven", "labels", "piece"),
            ("Care Label Printed", "labels", "piece"),
            ("Poly Bag 30x40", "packaging", "piece"),
            ("Sewing Thread 40/2 Navy", "thread", "cone"),
        ]
    }

    # Opening stock for trims so the inventory screens have depth.
    for name, qty in [("Main Label Woven", "12000"), ("Care Label Printed", "11500"),
                      ("Poly Bag 30x40", "8000"), ("Sewing Thread 40/2 Navy", "350")]:
        ok(c.post("/inventory/adjustments", json={
            "material_id": trims[name], "quantity": qty,
            "uom": "piece" if "Thread" not in name else "cone", "note": "opening balance",
        }), f"opening stock {name}")

    # ------------------------------------------------------------- styles
    def style_with_bom(number, description, consumption_base):
        style_id = ok(c.post("/styles", json={
            "style_number": number, "description": description, "size_range_id": sr,
            "gender": "mens", "standard_sam": "12.5",
        }), f"style {number}")["id"]
        cw = ok(c.post(f"/styles/{style_id}/colourways",
                       json={"colour_id": colours["NAVY"], "buyer_reference": f"BR-{number}"}),
                f"colourway {number}")
        ok(c.post(f"/styles/colourways/{cw['id']}/approve-lab-dip"), "lab dip")
        ok(c.post(f"/styles/{style_id}/colourways", json={"colour_id": colours["WHITE"]}), "colourway 2")
        steps = ["S", "M", "L", "XL"]
        lines = [{
            "material_id": fabric, "wastage_pct": "0.05",
            "size_consumption": [
                {"size_label": label, "consumption": str(consumption_base + i * 0.2)}
                for i, label in enumerate(steps)
            ],
        }]
        bom = ok(c.post(f"/styles/{style_id}/bom-versions", json={"lines": lines}), f"bom {number}")
        ok(c.post(f"/styles/bom-versions/{bom['id']}/approve"), f"bom approve {number}")
        sheet = ok(c.post(f"/costing/styles/{style_id}/cost-sheets", json={
            "base_size": "M", "sam": "12.5", "sewing_cost_per_min": "0.08",
            "sewing_efficiency_pct": "80", "overhead_pct": "0.15", "margin_pct": "0.20",
            "lines": [{"category": "material", "quantity": str(consumption_base + 0.2),
                       "rate": "4.00", "wastage_pct": "0.05"}],
        }), f"cost sheet {number}")
        ok(c.post(f"/costing/cost-sheets/{sheet['id']}/approve"), f"cost sheet approve {number}")
        return style_id

    tee = style_with_bom("TS-100", "Crew Neck Tee", 1.0)
    polo = style_with_bom("PL-200", "Classic Polo", 1.2)
    hoodie = style_with_bom("HD-300", "Pullover Hoodie", 1.8)

    # ----------------------------------------- order 1: shipped & settled
    order1 = ok(c.post("/sales-orders", json={
        "customer_id": customers["Northwind Outfitters"], "customer_po_number": "NW-PO-1042",
        "lines": [
            {"style_id": tee, "colour_id": colours["NAVY"], "unit_price": "10.00",
             "sizes": [{"size_label": "S", "ordered_qty": 50}, {"size_label": "M", "ordered_qty": 100},
                       {"size_label": "L", "ordered_qty": 100}, {"size_label": "XL", "ordered_qty": 50}]},
            {"style_id": tee, "colour_id": colours["WHITE"], "unit_price": "10.00",
             "sizes": [{"size_label": "M", "ordered_qty": 100}, {"size_label": "L", "ordered_qty": 100}]},
        ],
    }), "order 1")
    ok(c.post(f"/sales-orders/{order1['id']}/confirm"), "order 1 confirm")

    po = ok(c.post("/procurement/purchase-orders", json={
        "supplier_id": suppliers["Textile Mills Ltd"], "sales_order_id": order1["id"],
        "lines": [{"material_id": fabric, "ordered_qty": "1000", "unit_price": "4.00"}],
    }), "po 1")
    po_line = po["lines"][0]["id"]
    gr = ok(c.post("/procurement/goods-receipts", json={
        "purchase_order_id": po["id"], "client_key": "demo-gr-001",
        "rolls": [
            {"purchase_order_line_id": po_line, "length": "500", "width_cm": "150",
             "dye_lot": "DL-101", "shade_group": "SG-NAVY", "grade": "A"},
            {"purchase_order_line_id": po_line, "length": "500", "width_cm": "150",
             "dye_lot": "DL-101", "shade_group": "SG-NAVY", "grade": "A"},
        ],
    }), "goods receipt 1")
    for roll in gr["rolls"]:
        ok(c.post("/quality/four-point-inspections",
                  json={"roll_id": roll["roll_id"], "defects": [{"penalty_points": 2}]}),
           "roll inspection")

    cut = ok(c.post("/production/cut-orders", json={
        "style_id": tee, "colour_id": colours["NAVY"], "sales_order_id": order1["id"],
        "sizes": [{"size_label": "S", "planned_qty": 50}, {"size_label": "M", "planned_qty": 100},
                  {"size_label": "L", "planned_qty": 100}, {"size_label": "XL", "planned_qty": 50}],
    }), "cut order 1")
    ok(c.post(f"/production/cut-orders/{cut['id']}/reserve-fabric", json={"shade_group": "SG-NAVY"}), "reserve")
    ok(c.post(f"/production/cut-orders/{cut['id']}/issue-fabric", json={}), "issue")
    ok(c.post(f"/production/cut-orders/{cut['id']}/complete", json={"cut_qty": [
        {"size_label": "S", "planned_qty": 50}, {"size_label": "M", "planned_qty": 100},
        {"size_label": "L", "planned_qty": 99}, {"size_label": "XL", "planned_qty": 50}]}), "cut complete")

    sew = ok(c.post("/production/sewing-orders",
                    json={"style_id": tee, "line": "Line 1", "planned_qty": 299}), "sewing order")
    ok(c.post(f"/production/sewing-orders/{sew['id']}/daily-output", json={
        "output_date": "2026-07-20", "produced_qty": 150, "operators": 25, "working_minutes": 480}), "output d1")
    ok(c.post(f"/production/sewing-orders/{sew['id']}/daily-output", json={
        "output_date": "2026-07-21", "produced_qty": 149, "operators": 25, "working_minutes": 480}), "output d2")

    ok(c.post("/quality/inline-inspections",
              json={"sewing_order_id": sew["id"], "units_checked": 100, "defects_found": 3}), "inline")
    ok(c.post("/quality/final-inspections", json={
        "sales_order_id": order1["id"], "lot_size": 500, "aql": "2.5", "defects_found": 2}), "final aql")
    ok(c.post(f"/sales-orders/{order1['id']}/ship"), "ship order 1")

    invoices = ok(c.get("/finance/ar-invoices"), "invoices")
    ok(c.post(f"/finance/ar-invoices/{invoices[0]['id']}/settle",
              json={"amount": invoices[0]["amount"]}), "settle")

    # -------------------------------------------- order 2: mid-production
    order2 = ok(c.post("/sales-orders", json={
        "customer_id": customers["Atlantic Basics GmbH"], "customer_po_number": "AB-PO-2210",
        "currency": "EUR",
        "lines": [{"style_id": polo, "colour_id": colours["NAVY"], "unit_price": "14.50",
                   "sizes": [{"size_label": "M", "ordered_qty": 200}, {"size_label": "L", "ordered_qty": 200}]}],
    }), "order 2")
    ok(c.post(f"/sales-orders/{order2['id']}/confirm"), "order 2 confirm")
    po2 = ok(c.post("/procurement/purchase-orders", json={
        "supplier_id": suppliers["Textile Mills Ltd"], "sales_order_id": order2["id"],
        "lines": [{"material_id": fabric, "ordered_qty": "700", "unit_price": "4.10"}],
    }), "po 2")
    gr2 = ok(c.post("/procurement/goods-receipts", json={
        "purchase_order_id": po2["id"], "client_key": "demo-gr-002",
        "rolls": [{"purchase_order_line_id": po2["lines"][0]["id"], "length": "500",
                   "width_cm": "160", "dye_lot": "DL-201", "shade_group": "SG-NAVY2", "grade": "A"}],
    }), "goods receipt 2")
    ok(c.post("/quality/four-point-inspections",
              json={"roll_id": gr2["rolls"][0]["roll_id"], "defects": []}), "roll inspection 2")

    # ------------------------------------------------- order 3: enquiry
    ok(c.post("/sales-orders", json={
        "customer_id": customers["Sierra Sportswear"], "customer_po_number": "SS-DRAFT-17",
        "lines": [{"style_id": hoodie, "colour_id": colours["BLACK"], "unit_price": "22.00",
                   "sizes": [{"size_label": "M", "ordered_qty": 150}, {"size_label": "L", "ordered_qty": 150}]}],
    }), "order 3")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--password",
        help="Password for every demo user (default: generated and printed)",
    )
    args = parser.parse_args()
    password = args.password or f"Demo-{secrets.token_urlsafe(9)}"

    seed(password)

    print("\nDemo factory seeded successfully.")
    print(f"  Login domain: @{DEMO_DOMAIN}   Password (all users): {password}")
    for handle, role in DEMO_USERS:
        print(f"  {handle + '@' + DEMO_DOMAIN:38s} {role.value}")


if __name__ == "__main__":
    main()
