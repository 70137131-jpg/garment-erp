import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ..config import settings
from ..kernel.audit import utcnow
from ..kernel.rbac import Role
from .models import AuthSession, LoginThrottle, SecurityAuditEvent, User, UserRole
from .passwords import validate_password


_password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


# Verify this hash for unknown accounts so the login endpoint does not reveal
# whether an email exists through a cheap-vs-Argon2 timing difference.
DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _password_hasher.check_needs_rehash(password_hash)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def get_roles(session: Session, user_id: int) -> set[Role]:
    names = session.exec(select(UserRole.role).where(UserRole.user_id == user_id)).all()
    return {Role(name) for name in names if name in Role._value2member_map_}


def replace_roles(session: Session, user_id: int, roles: set[Role], granted_by: int | None) -> None:
    existing = session.exec(select(UserRole).where(UserRole.user_id == user_id)).all()
    for assignment in existing:
        session.delete(assignment)
    for role in sorted(roles, key=lambda item: item.value):
        session.add(UserRole(user_id=user_id, role=role.value, granted_by=granted_by))


def create_session(
    session: Session, user_id: int, ip_address: str | None, user_agent: str | None
) -> str:
    token = secrets.token_urlsafe(48)
    session.add(
        AuthSession(
            token_hash=token_digest(token),
            user_id=user_id,
            expires_at=utcnow() + timedelta(hours=settings.session_ttl_hours),
            ip_address=ip_address,
            user_agent=(user_agent or "")[:512] or None,
        )
    )
    return token


def consume_login_attempt(session: Session, ip_address: str | None) -> int | None:
    """Consume one attempt from a shared, database-backed IP window.

    Returns the number of seconds until retry when the window is exhausted.
    The caller owns the transaction so failure auditing and throttling commit
    atomically.
    """
    raw_key = ip_address or "unknown"
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    now = utcnow()
    window = timedelta(minutes=settings.login_ip_window_minutes)
    bucket = session.exec(
        select(LoginThrottle).where(LoginThrottle.key_hash == key_hash).with_for_update()
    ).first()
    if bucket is None:
        session.add(LoginThrottle(key_hash=key_hash, window_started_at=now, attempts=1))
        try:
            # Flush now so two simultaneous first requests from the same IP do
            # not defer a uniqueness failure until after password verification.
            session.flush()
        except IntegrityError:
            session.rollback()
            bucket = session.exec(
                select(LoginThrottle)
                .where(LoginThrottle.key_hash == key_hash)
                .with_for_update()
            ).first()
            if bucket is None:
                # The competing transaction may have rolled back. Consume this
                # attempt in a fresh row rather than failing the login endpoint.
                session.add(LoginThrottle(key_hash=key_hash, window_started_at=now, attempts=1))
                session.flush()
                return None
            started = as_utc(bucket.window_started_at)
            if started + window <= now:
                bucket.window_started_at = now
                bucket.attempts = 1
            elif bucket.attempts >= settings.login_ip_max_attempts:
                return max(1, int((started + window - now).total_seconds()))
            else:
                bucket.attempts += 1
            session.add(bucket)
        return None
    started = as_utc(bucket.window_started_at)
    if started + window <= now:
        bucket.window_started_at = now
        bucket.attempts = 1
        session.add(bucket)
        return None
    if bucket.attempts >= settings.login_ip_max_attempts:
        return max(1, int((started + window - now).total_seconds()))
    bucket.attempts += 1
    session.add(bucket)
    return None


def clear_login_throttle(session: Session, ip_address: str | None) -> None:
    key_hash = hashlib.sha256((ip_address or "unknown").encode("utf-8")).hexdigest()
    bucket = session.get(LoginThrottle, key_hash)
    if bucket is not None:
        session.delete(bucket)


def resolve_session(session: Session, token: str) -> tuple[User, AuthSession] | None:
    auth_session = session.exec(
        select(AuthSession).where(AuthSession.token_hash == token_digest(token))
    ).first()
    now = utcnow()
    if auth_session is None or auth_session.revoked_at is not None or as_utc(auth_session.expires_at) <= now:
        return None
    if as_utc(auth_session.last_seen_at) + timedelta(minutes=settings.session_idle_minutes) <= now:
        auth_session.revoked_at = now
        session.add(auth_session)
        audit(session, "session_idle_expired", actor_user_id=auth_session.user_id)
        session.commit()
        return None
    user = session.get(User, auth_session.user_id)
    if user is None or not user.is_active:
        return None
    if as_utc(auth_session.last_seen_at) + timedelta(minutes=settings.session_touch_minutes) <= now:
        auth_session.last_seen_at = now
        session.add(auth_session)
        session.commit()
    return user, auth_session


def revoke_user_sessions(
    session: Session, user_id: int, except_session_id: int | None = None
) -> None:
    now = utcnow()
    active = session.exec(
        select(AuthSession).where(
            AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None)
        )
    ).all()
    for auth_session in active:
        if auth_session.id == except_session_id:
            continue
        auth_session.revoked_at = now
        session.add(auth_session)


def reset_user_password(
    session: Session,
    user: User,
    temporary_password: str,
    *,
    actor_user_id: int | None = None,
) -> None:
    """Set a temporary password, unlock the account, and revoke all sessions."""
    validate_password(temporary_password)
    user.password_hash = hash_password(temporary_password)
    user.must_change_password = True
    user.failed_login_attempts = 0
    user.locked_until = None
    user.updated_at = utcnow()
    session.add(user)
    revoke_user_sessions(session, user.id)
    audit(
        session,
        "admin_password_reset",
        actor_user_id=actor_user_id,
        target_user_id=user.id,
    )


def audit(
    session: Session,
    event_type: str,
    *,
    actor_user_id: int | None = None,
    target_user_id: int | None = None,
    email: str | None = None,
    ip_address: str | None = None,
    detail: str | None = None,
) -> None:
    session.add(
        SecurityAuditEvent(
            event_type=event_type,
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
            email=email,
            ip_address=ip_address,
            detail=detail,
        )
    )


def bootstrap_admin(session: Session) -> None:
    """Create the first admin only when explicit deployment credentials exist."""
    email = settings.bootstrap_admin_email
    password = settings.bootstrap_admin_password
    if not email and not password:
        return
    if not email or not password:
        raise RuntimeError(
            "BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD must be configured together"
        )
    try:
        validate_password(password)
    except ValueError as exc:
        raise RuntimeError(f"Invalid bootstrap administrator password: {exc}") from exc
    if session.exec(select(User.id)).first() is not None:
        return
    user = User(
        email=normalize_email(email),
        display_name=settings.bootstrap_admin_name.strip() or "System Administrator",
        password_hash=hash_password(password),
        must_change_password=True,
    )
    session.add(user)
    session.flush()
    session.add(UserRole(user_id=user.id, role=Role.admin.value, granted_by=user.id))
    audit(session, "bootstrap_admin_created", actor_user_id=user.id, target_user_id=user.id)
    session.commit()
