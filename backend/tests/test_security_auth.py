from datetime import timedelta

from sqlmodel import select

from app.kernel.rbac import Role, get_current_principal
from app.config import settings
from app.security.models import AuthSession, SecurityAuditEvent, User, UserRole
from app.security.service import hash_password
from app.kernel.audit import utcnow


PASSWORD = "Correct-Horse-Battery-Staple-42"


def _user(session, email="operator@example.com", role=Role.merchandiser, must_change=False):
    user = User(
        email=email,
        display_name="ERP Operator",
        password_hash=hash_password(PASSWORD),
        must_change_password=must_change,
    )
    session.add(user)
    session.flush()
    session.add(UserRole(user_id=user.id, role=role.value, granted_by=user.id))
    session.commit()
    return user


def _use_real_auth(client):
    client.app.dependency_overrides.pop(get_current_principal, None)


def test_business_routes_require_authentication(client):
    _use_real_auth(client)
    response = client.get("/masters/materials", headers={"X-Role": "admin"})
    assert response.status_code == 401


def test_login_sets_httponly_session_and_logout_revokes_it(client, session):
    user = _user(session)
    _use_real_auth(client)
    response = client.post("/auth/login", json={"email": user.email.upper(), "password": PASSWORD})
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    assert response.json()["roles"] == ["merchandiser"]
    assert client.get("/auth/me").status_code == 200
    assert client.post("/auth/logout").status_code == 204
    assert client.get("/auth/me").status_code == 401
    persisted = session.exec(select(AuthSession).where(AuthSession.user_id == user.id)).one()
    session.refresh(persisted)
    assert persisted.revoked_at is not None


def test_failed_login_is_generic_and_audited(client, session):
    user = _user(session)
    _use_real_auth(client)
    response = client.post("/auth/login", json={"email": user.email, "password": "wrong"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"
    event = session.exec(select(SecurityAuditEvent).where(SecurityAuditEvent.event_type == "login_failed")).one()
    assert event.target_user_id == user.id


def test_repeated_failures_lock_the_account(client, session, monkeypatch):
    user = _user(session)
    _use_real_auth(client)
    monkeypatch.setattr(settings, "login_max_attempts", 2)
    for _ in range(2):
        assert client.post("/auth/login", json={"email": user.email, "password": "wrong"}).status_code == 401
    assert client.post("/auth/login", json={"email": user.email, "password": PASSWORD}).status_code == 401
    session.refresh(user)
    assert user.locked_until is not None


def test_module_permission_denial_is_audited(client, session):
    user = _user(session, role=Role.stores)
    _use_real_auth(client)
    assert client.post("/auth/login", json={"email": user.email, "password": PASSWORD}).status_code == 200
    assert client.get("/finance/accounts").status_code == 403
    event = session.exec(select(SecurityAuditEvent).where(SecurityAuditEvent.event_type == "authorization_denied")).one()
    assert event.actor_user_id == user.id
    assert event.detail == "required_permission=finance:read"


def test_admin_can_create_user_and_duplicate_email_is_rejected(client):
    payload = {"email": "planner@example.com", "display_name": "Production Planner", "password": PASSWORD, "roles": ["planner"]}
    response = client.post("/auth/users", json=payload)
    assert response.status_code == 201, response.text
    assert response.json()["roles"] == ["planner"]
    assert client.post("/auth/users", json=payload).status_code == 409


def test_last_active_admin_cannot_be_disabled(client, session):
    admin = _user(session, "admin@example.com", Role.admin)
    response = client.patch(f"/auth/users/{admin.id}", json={"is_active": False})
    assert response.status_code == 409


def test_csrf_is_required_and_security_headers_are_present(client):
    token = client.headers.pop("X-CSRF-Token")
    rejected = client.post("/auth/login", json={"email": "nobody@example.com", "password": PASSWORD})
    assert rejected.status_code == 403
    assert rejected.json()["detail"] == "CSRF validation failed"
    client.headers["X-CSRF-Token"] = token
    response = client.get("/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["content-security-policy"].startswith("default-src 'none'")
    assert response.headers["x-request-id"]


def test_request_body_limit_is_enforced(client, monkeypatch):
    monkeypatch.setattr(settings, "max_request_body_bytes", 8)
    response = client.post("/auth/login", content=b'{"too":"large"}', headers={"Content-Type": "application/json"})
    assert response.status_code == 413


def test_password_policy_rejects_short_and_common_passwords(client):
    base = {"email": "new@example.com", "display_name": "New User", "roles": ["readonly"]}
    short = client.post("/auth/users", json={**base, "password": "short-password"})
    assert short.status_code == 422
    common = client.post("/auth/users", json={**base, "password": "password1234567"})
    assert common.status_code == 422


def test_temporary_password_must_be_replaced_before_business_access(client, session):
    user = _user(session, must_change=True)
    _use_real_auth(client)
    assert client.post("/auth/login", json={"email": user.email, "password": PASSWORD}).status_code == 200
    blocked = client.get("/masters/materials")
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "Password change required"
    changed = client.post(
        "/auth/password",
        json={"current_password": PASSWORD, "new_password": "A-new-long-passphrase-2026"},
    )
    assert changed.status_code == 204, changed.text
    assert client.get("/masters/materials").status_code == 200
    session.refresh(user)
    assert user.must_change_password is False


def test_idle_session_expires_and_is_audited(client, session, monkeypatch):
    user = _user(session)
    _use_real_auth(client)
    monkeypatch.setattr(settings, "session_idle_minutes", 1)
    assert client.post("/auth/login", json={"email": user.email, "password": PASSWORD}).status_code == 200
    auth_session = session.exec(select(AuthSession).where(AuthSession.user_id == user.id)).one()
    auth_session.last_seen_at = utcnow() - timedelta(minutes=2)
    session.add(auth_session)
    session.commit()
    assert client.get("/auth/me").status_code == 401
    event = session.exec(
        select(SecurityAuditEvent).where(SecurityAuditEvent.event_type == "session_idle_expired")
    ).one()
    assert event.actor_user_id == user.id


def test_ip_login_throttle_covers_unknown_accounts(client, monkeypatch):
    _use_real_auth(client)
    monkeypatch.setattr(settings, "login_ip_max_attempts", 1)
    payload = {"email": "unknown@example.com", "password": PASSWORD}
    assert client.post("/auth/login", json=payload).status_code == 401
    throttled = client.post("/auth/login", json=payload)
    assert throttled.status_code == 429
    assert int(throttled.headers["retry-after"]) > 0
