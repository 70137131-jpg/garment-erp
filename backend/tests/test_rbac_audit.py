"""Module 9 — access control, segregation of duties, and audit/immutability."""

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from sqlmodel import select

from tests.factories import make_fabric, make_size_range, make_supplier
from app.db import get_session
from app.kernel.rbac import Principal, Role, get_current_principal
from app.kernel.immutability import install_immutable_record_guards
from app.security.models import SecurityAuditEvent, User


def _act_as(client, *roles: Role):
    # The operator must exist as a real row: denial audit events reference
    # app_user by foreign key, which PostgreSQL enforces.
    session = client.app.dependency_overrides[get_session]()
    user = session.exec(select(User).where(User.email == "operator@test.local")).first()
    if user is None:
        user = User(
            email="operator@test.local",
            display_name="Test Operator",
            password_hash="!test-fixture-no-login",
            must_change_password=False,
        )
        session.add(user)
        session.flush()
    user_id = user.id
    client.app.dependency_overrides[get_current_principal] = lambda: Principal(
        user_id=user_id,
        email="operator@test.local",
        display_name="Test Operator",
        roles=frozenset(roles),
        session_id=10,
    )


def _style_payload(sr):
    return {"style_number": "TS-900", "description": "Tee", "size_range_id": sr}


def test_role_gate_rejects_wrong_role(client):
    sr = make_size_range(client)
    _act_as(client, Role.stores)
    # Stores cannot create styles (merchandiser duty).
    r = client.post("/styles", json=_style_payload(sr), headers={"X-Role": "stores"})
    assert r.status_code == 403


def test_role_gate_allows_correct_role(client):
    sr = make_size_range(client)
    _act_as(client, Role.merchandiser)
    r = client.post("/styles", json=_style_payload(sr), headers={"X-Role": "merchandiser"})
    assert r.status_code == 201


def test_admin_bypasses_role_gates(client):
    sr = make_size_range(client)
    # Default header is admin; admin is permitted everywhere.
    r = client.post("/styles", json=_style_payload(sr))
    assert r.status_code == 201


def test_segregation_finance_only_posts_journals(finance_client):
    payload = {"memo": "x", "lines": [
        {"account_code": "1200", "debit": "100"},
        {"account_code": "3000", "credit": "100"}]}
    # Procurement cannot touch the ledger.
    _act_as(finance_client, Role.procurement)
    r = finance_client.post("/finance/journal-entries", json=payload, headers={"X-Role": "finance"})
    assert r.status_code == 403
    # Finance can.
    _act_as(finance_client, Role.finance)
    r = finance_client.post("/finance/journal-entries", json=payload, headers={"X-Role": "finance"})
    assert r.status_code == 201


def test_spoofed_role_header_cannot_escalate(client):
    sr = make_size_range(client)
    _act_as(client, Role.stores)
    r = client.post("/styles", json=_style_payload(sr), headers={"X-Role": "admin"})
    assert r.status_code == 403


def test_stock_ledger_is_append_only_audit(client):
    """Corrections are new lines; nothing is mutated or deleted (blueprint 4.1)."""
    mat = make_fabric(client)
    client.post("/inventory/adjustments", json={"material_id": mat, "quantity": "100", "note": "open"})
    client.post("/inventory/adjustments", json={"material_id": mat, "quantity": "-30", "note": "damage"})
    client.post("/inventory/adjustments", json={"material_id": mat, "quantity": "10", "note": "found"})
    ledger = client.get("/inventory/ledger", params={"material_id": mat}).json()
    # Three immutable lines; there is no update/delete endpoint on the ledger.
    assert len(ledger) == 3
    assert [Decimal(l["quantity"]) for l in ledger] == [Decimal("100"), Decimal("-30"), Decimal("10")]
    bal = client.get("/inventory/balance", params={"material_id": mat}).json()
    assert Decimal(bal["on_hand"]) == Decimal("80")


def test_posted_journal_immutable_via_reversal(finance_client):
    """Posted journals can only be corrected by a reversing entry (8.2)."""
    created = finance_client.post("/finance/journal-entries", json={"memo": "e", "lines": [
        {"account_code": "1000", "debit": "500"}, {"account_code": "2000", "credit": "500"}]},
        headers={"X-Role": "finance"}).json()
    rev = finance_client.post(f"/finance/journal-entries/{created['id']}/reverse",
                              headers={"X-Role": "finance"})
    assert rev.status_code == 200
    original = finance_client.get(f"/finance/journal-entries/{created['id']}").json()
    assert original["status"] == "reversed"


def test_database_guards_reject_direct_ledger_and_audit_tampering(client, finance_client, session):
    """The append-only rule still holds when application endpoints are bypassed."""
    install_immutable_record_guards(session.get_bind())
    material_id = make_fabric(client)
    assert client.post(
        "/inventory/adjustments", json={"material_id": material_id, "quantity": "5", "note": "opening"}
    ).status_code == 201
    ledger_id = client.get("/inventory/ledger", params={"material_id": material_id}).json()[0]["id"]

    audit_event = SecurityAuditEvent(event_type="test_event")
    session.add(audit_event)
    session.commit()

    for statement in (
        text("UPDATE stock_ledger_entry SET quantity = 999 WHERE id = :id"),
        text("DELETE FROM stock_ledger_entry WHERE id = :id"),
    ):
        with pytest.raises(IntegrityError, match="Immutable record"):
            session.execute(statement, {"id": ledger_id})
            session.commit()
        session.rollback()

    for statement in (
        text("UPDATE security_audit_event SET event_type = 'changed' WHERE id = :id"),
        text("DELETE FROM security_audit_event WHERE id = :id"),
    ):
        with pytest.raises(IntegrityError, match="Immutable record"):
            session.execute(statement, {"id": audit_event.id})
            session.commit()
        session.rollback()

    created = finance_client.post("/finance/journal-entries", json={"memo": "guard", "lines": [
        {"account_code": "1000", "debit": "10"}, {"account_code": "2000", "credit": "10"}
    ]}).json()
    journal_line_id = session.execute(
        text("SELECT id FROM journal_line WHERE journal_entry_id = :id"), {"id": created["id"]}
    ).scalars().first()
    assert journal_line_id is not None
    with pytest.raises(IntegrityError, match="Immutable record"):
        session.execute(text("UPDATE journal_line SET debit = 20 WHERE id = :id"), {"id": journal_line_id})
        session.commit()
    session.rollback()

    # The one permitted state change is a reversing entry; no journal history is edited.
    assert finance_client.post(f"/finance/journal-entries/{created['id']}/reverse").status_code == 200
