"""CSV onboarding imports: dry-run reports, all-or-nothing apply, templates."""

from decimal import Decimal

from sqlmodel import select

from app.masters.models import Customer, Material, Supplier

from tests.factories import make_fabric


def _upload(client, kind, csv_text, apply=False):
    return client.post(
        f"/imports/{kind}",
        params={"apply": str(apply).lower()},
        files={"file": (f"{kind}.csv", csv_text.encode(), "text/csv")},
    )


def test_template_download(client):
    response = client.get("/imports/templates/customers")
    assert response.status_code == 200
    assert response.text.strip() == "name,currency,payment_terms,credit_limit,billing_address"
    assert client.get("/imports/templates/nonsense").status_code == 404


def test_customer_import_dry_run_creates_nothing(client, session):
    report = _upload(client, "customers", "name,currency\nAcme Fashion,USD\nZara Knits,EUR\n").json()
    assert report == {
        "kind": "customers", "total_rows": 2, "valid_rows": 2,
        "created": 0, "applied": False, "errors": [],
    }
    assert session.exec(select(Customer)).all() == []


def test_customer_import_apply_and_rerun_reports_duplicates(client, session):
    csv_text = "name,currency,credit_limit\nAcme Fashion,USD,5000\nZara Knits,EUR,\n"
    report = _upload(client, "customers", csv_text, apply=True).json()
    assert report["applied"] is True
    assert report["created"] == 2
    customers = session.exec(select(Customer)).all()
    assert {c.name for c in customers} == {"Acme Fashion", "Zara Knits"}
    assert all(c.code.startswith("CUST-") for c in customers)
    assert next(c for c in customers if c.name == "Acme Fashion").credit_limit == Decimal("5000")

    # Second run: both rows now exist → nothing applied.
    rerun = _upload(client, "customers", csv_text, apply=True).json()
    assert rerun["applied"] is False
    assert rerun["created"] == 0
    assert len(rerun["errors"]) == 2
    assert "already exists" in rerun["errors"][0]["message"]


def test_apply_is_all_or_nothing_when_any_row_fails(client, session):
    csv_text = "name,currency\nGood Row,USD\n,EUR\n"  # second row has no name
    report = _upload(client, "customers", csv_text, apply=True).json()
    assert report["applied"] is False
    assert report["errors"][0]["row"] == 2
    assert session.exec(select(Customer)).all() == []


def test_unknown_columns_are_rejected(client):
    report = _upload(client, "customers", "name,tax_number\nAcme,123\n").json()
    assert report["errors"][0]["row"] == 0
    assert "Unknown columns: tax_number" in report["errors"][0]["message"]


def test_supplier_import_with_material_types(client, session):
    csv_text = (
        "name,currency,lead_time_days,material_types\n"
        "Dyeworks Ltd,USD,14,fabric|chemicals\n"
    )
    report = _upload(client, "suppliers", csv_text, apply=True).json()
    assert report["applied"] is True
    supplier = session.exec(select(Supplier)).one()
    assert supplier.lead_time_days == 14
    assert {mt.material_type.value for mt in supplier.material_types} == {"fabric", "chemicals"}


def test_material_import_validates_enum_and_applies(client, session):
    bad = _upload(client, "materials", "name,material_type\nMystery Item,plutonium\n").json()
    assert bad["applied"] is False
    assert "material_type" in bad["errors"][0]["message"]

    good = _upload(
        client,
        "materials",
        "name,material_type,base_uom,gsm\nSingle Jersey 180,fabric,metre,180\nCare Label,labels,piece,\n",
        apply=True,
    ).json()
    assert good["created"] == 2
    materials = session.exec(select(Material)).all()
    codes = sorted(m.code.split("-")[0] for m in materials)
    assert codes == ["FAB", "LBL"]


def test_opening_stock_import_posts_ledger_entries(client, session):
    material_id = make_fabric(client)
    material = session.get(Material, material_id)

    unknown = _upload(client, "opening-stock", "material_code,quantity\nNOPE-1,10\n", apply=True).json()
    assert unknown["applied"] is False
    assert "unknown material_code" in unknown["errors"][0]["message"]

    report = _upload(
        client,
        "opening-stock",
        f"material_code,quantity,warehouse\n{material.code},250.5,MAIN\n",
        apply=True,
    ).json()
    assert report["applied"] is True
    balance = client.get("/inventory/balance", params={"material_id": material_id}).json()
    assert Decimal(balance["on_hand"]) == Decimal("250.5")


def test_opening_stock_rejects_non_positive_quantities(client):
    fabric = make_fabric(client)
    del fabric
    report = _upload(client, "opening-stock", "material_code,quantity\nFAB-XXX,-5\n").json()
    assert len(report["errors"]) == 1


def test_imports_require_admin_role(client, session):
    from app.kernel.rbac import Principal, Role, get_current_principal
    from app.security.models import User

    # A real row, because the denial audit references app_user by FK.
    stores_user = User(
        email="stores@test.local",
        display_name="Stores",
        password_hash="!test-fixture-no-login",
        must_change_password=False,
    )
    session.add(stores_user)
    session.commit()
    client.app.dependency_overrides[get_current_principal] = lambda: Principal(
        user_id=stores_user.id,
        email=stores_user.email,
        display_name=stores_user.display_name,
        roles=frozenset({Role.stores}),
        session_id=1,
    )
    response = _upload(client, "customers", "name\nAcme\n", apply=True)
    assert response.status_code == 403
