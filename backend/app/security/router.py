from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ..config import settings
from ..db import get_session
from ..kernel.audit import utcnow
from ..kernel.rbac import (
    ROLE_PERMISSIONS,
    Permission,
    Principal,
    Role,
    get_current_principal,
    require_permissions,
)
from .middleware import set_csrf_cookie
from .models import (
    AuthSession,
    AuthSessionRead,
    ChangePasswordRequest,
    LoginRequest,
    PasswordResetRequest,
    SecurityAuditEvent,
    SecurityAuditEventRead,
    User,
    UserCreate,
    UserRead,
    UserRole,
    UserUpdate,
)
from .service import (
    as_utc,
    audit,
    create_session,
    clear_login_throttle,
    consume_login_attempt,
    DUMMY_PASSWORD_HASH,
    get_roles,
    hash_password,
    normalize_email,
    password_needs_rehash,
    replace_roles,
    reset_user_password,
    revoke_user_sessions,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["authentication"])


def _client_ip(request: Request) -> str | None:
    # Do not trust X-Forwarded-For here unless a deployment-level trusted proxy
    # has normalized it. request.client is safe and still useful for audit.
    return request.client.host if request.client else None


def _parse_roles(values: list[str]) -> set[Role]:
    try:
        roles = {Role(value) for value in values}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="One or more roles are invalid") from exc
    if not roles:
        raise HTTPException(status_code=422, detail="At least one role is required")
    return roles


def _read_user(session: Session, user: User, include_permissions: bool = False) -> UserRead:
    roles = get_roles(session, user.id)
    permissions = sorted(
        {p.value for role in roles for p in ROLE_PERMISSIONS.get(role, set())}
    ) if include_permissions else []
    return UserRead(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        is_active=user.is_active,
        must_change_password=user.must_change_password,
        roles=sorted(role.value for role in roles),
        permissions=permissions,
        created_at=user.created_at,
    )


@router.get("/csrf", status_code=204)
def csrf(response: Response):
    set_csrf_cookie(response)


@router.post("/login", response_model=UserRead)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
):
    email = normalize_email(payload.email)
    ip_address = _client_ip(request)
    retry_after = consume_login_attempt(session, ip_address)
    if retry_after is not None:
        audit(session, "login_throttled", email=email, ip_address=ip_address)
        session.commit()
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )
    user = session.exec(select(User).where(User.email == email).with_for_update()).first()
    now = utcnow()
    locked = user is not None and user.locked_until is not None and as_utc(user.locked_until) > now
    password_valid = verify_password(user.password_hash if user else DUMMY_PASSWORD_HASH, payload.password)
    valid = user is not None and not locked and user.is_active and password_valid
    if not valid:
        if user is not None and not locked:
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= settings.login_max_attempts:
                user.locked_until = now + timedelta(minutes=settings.login_lockout_minutes)
            user.updated_at = now
            session.add(user)
        audit(session, "login_failed", target_user_id=user.id if user else None, email=email, ip_address=ip_address)
        session.commit()
        raise HTTPException(status_code=401, detail="Invalid email or password")

    user.failed_login_attempts = 0
    user.locked_until = None
    user.updated_at = now
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)
    token = create_session(session, user.id, ip_address, request.headers.get("User-Agent"))
    clear_login_throttle(session, ip_address)
    audit(session, "login_succeeded", actor_user_id=user.id, email=email, ip_address=ip_address)
    session.add(user)
    session.commit()
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="strict",
        path="/",
    )
    set_csrf_cookie(response, request.cookies.get(settings.csrf_cookie_name))
    return _read_user(session, user, include_permissions=True)


@router.post("/logout", status_code=204)
def logout(
    response: Response,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
):
    auth_session = session.get(AuthSession, principal.session_id)
    if auth_session is not None and auth_session.revoked_at is None:
        auth_session.revoked_at = utcnow()
        session.add(auth_session)
    audit(session, "logout", actor_user_id=principal.user_id)
    session.commit()
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(settings.csrf_cookie_name, path="/")


@router.get("/me", response_model=UserRead)
def me(
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
):
    user = session.get(User, principal.user_id)
    return _read_user(session, user, include_permissions=True)


@router.post("/password", status_code=204)
def change_password(
    payload: ChangePasswordRequest,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
):
    user = session.get(User, principal.user_id)
    if user is None or not verify_password(user.password_hash, payload.current_password):
        audit(session, "password_change_failed", actor_user_id=principal.user_id)
        session.commit()
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if verify_password(user.password_hash, payload.new_password):
        raise HTTPException(status_code=422, detail="New password must be different")
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    user.failed_login_attempts = 0
    user.locked_until = None
    user.updated_at = utcnow()
    session.add(user)
    revoke_user_sessions(session, user.id, except_session_id=principal.session_id)
    audit(session, "password_changed", actor_user_id=user.id, target_user_id=user.id)
    session.commit()


@router.get(
    "/users",
    response_model=list[UserRead],
    dependencies=[Depends(require_permissions(Permission.users_manage))],
)
def list_users(session: Session = Depends(get_session)):
    return [_read_user(session, user) for user in session.exec(select(User).order_by(User.email)).all()]


@router.post(
    "/users",
    response_model=UserRead,
    status_code=201,
    dependencies=[Depends(require_permissions(Permission.users_manage))],
)
def create_user(
    payload: UserCreate,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
):
    roles = _parse_roles(payload.roles)
    user = User(
        email=normalize_email(payload.email),
        display_name=payload.display_name,
        password_hash=hash_password(payload.password),
    )
    session.add(user)
    try:
        session.flush()
        replace_roles(session, user.id, roles, principal.user_id)
        audit(session, "user_created", actor_user_id=principal.user_id, target_user_id=user.id, detail=",".join(sorted(r.value for r in roles)))
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="A user with this email already exists") from exc
    session.refresh(user)
    return _read_user(session, user)


@router.post(
    "/users/{user_id}/reset-password",
    status_code=204,
    dependencies=[Depends(require_permissions(Permission.users_manage))],
)
def reset_password(
    user_id: int,
    payload: PasswordResetRequest,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
):
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    reset_user_password(
        session,
        user,
        payload.temporary_password,
        actor_user_id=principal.user_id,
    )
    session.commit()


@router.get(
    "/sessions",
    response_model=list[AuthSessionRead],
    dependencies=[Depends(require_permissions(Permission.users_manage))],
)
def list_sessions(
    user_id: int | None = None,
    active_only: bool = False,
    session: Session = Depends(get_session),
):
    statement = select(AuthSession)
    if user_id is not None:
        statement = statement.where(AuthSession.user_id == user_id)
    if active_only:
        statement = statement.where(
            AuthSession.revoked_at.is_(None), AuthSession.expires_at > utcnow()
        )
    return session.exec(statement.order_by(AuthSession.id.desc())).all()


@router.post(
    "/sessions/{session_id}/revoke",
    status_code=204,
    dependencies=[Depends(require_permissions(Permission.users_manage))],
)
def revoke_session(
    session_id: int,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
):
    auth_session = session.get(AuthSession, session_id)
    if auth_session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if auth_session.revoked_at is None:
        auth_session.revoked_at = utcnow()
        session.add(auth_session)
        audit(
            session,
            "admin_session_revoked",
            actor_user_id=principal.user_id,
            target_user_id=auth_session.user_id,
            detail=f"session_id={auth_session.id}",
        )
        session.commit()


@router.get(
    "/audit-events",
    response_model=list[SecurityAuditEventRead],
    dependencies=[Depends(require_permissions(Permission.users_manage))],
)
def list_audit_events(
    limit: int = Query(default=200, ge=1, le=1000),
    event_type: str | None = None,
    user_id: int | None = None,
    session: Session = Depends(get_session),
):
    statement = select(SecurityAuditEvent)
    if event_type:
        statement = statement.where(SecurityAuditEvent.event_type == event_type)
    if user_id is not None:
        statement = statement.where(
            (SecurityAuditEvent.actor_user_id == user_id)
            | (SecurityAuditEvent.target_user_id == user_id)
        )
    return session.exec(
        statement.order_by(SecurityAuditEvent.id.desc()).limit(limit)
    ).all()


def _active_admin_count(session: Session) -> int:
    return len(session.exec(
        select(User.id).join(UserRole, UserRole.user_id == User.id).where(
            User.is_active.is_(True), UserRole.role == Role.admin.value
        ).with_for_update()
    ).all())


@router.patch(
    "/users/{user_id}",
    response_model=UserRead,
    dependencies=[Depends(require_permissions(Permission.users_manage))],
)
def update_user(
    user_id: int,
    payload: UserUpdate,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
):
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    current_roles = get_roles(session, user_id)
    new_roles = _parse_roles(payload.roles) if payload.roles is not None else current_roles
    removing_last_admin = (
        Role.admin in current_roles
        and (Role.admin not in new_roles or payload.is_active is False)
        and _active_admin_count(session) <= 1
    )
    if removing_last_admin:
        raise HTTPException(status_code=409, detail="The last active administrator cannot be removed")
    if payload.display_name is not None:
        name = payload.display_name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="Display name is required")
        user.display_name = name
    if payload.is_active is not None:
        user.is_active = payload.is_active
    if payload.password is not None:
        user.password_hash = hash_password(payload.password)
        user.must_change_password = True
    if payload.roles is not None:
        replace_roles(session, user.id, new_roles, principal.user_id)
    user.updated_at = utcnow()
    session.add(user)
    # Any security-sensitive change terminates existing sessions immediately.
    if payload.password is not None or payload.roles is not None or payload.is_active is False:
        revoke_user_sessions(session, user.id)
    audit(session, "user_updated", actor_user_id=principal.user_id, target_user_id=user.id)
    session.commit()
    session.refresh(user)
    return _read_user(session, user)
