from dataclasses import dataclass
from enum import Enum

from fastapi import Depends, HTTPException, Request, status
from sqlmodel import Session

from ..config import settings
from ..db import get_session


class Role(str, Enum):
    admin = "admin"
    merchandiser = "merchandiser"
    procurement = "procurement"
    stores = "stores"
    cutting_supervisor = "cutting_supervisor"
    sewing_supervisor = "sewing_supervisor"
    quality_inspector = "quality_inspector"
    finance = "finance"
    planner = "planner"
    readonly = "readonly"


class Permission(str, Enum):
    masters_read = "masters:read"
    styles_read = "styles:read"
    sales_read = "sales:read"
    procurement_read = "procurement:read"
    inventory_read = "inventory:read"
    production_read = "production:read"
    quality_read = "quality:read"
    costing_read = "costing:read"
    finance_read = "finance:read"
    planning_read = "planning:read"
    users_manage = "users:manage"
    # Workforce data (attendance, piece rates) is HR-sensitive: deliberately
    # excluded from _ALL_READ and granted role-by-role.
    workforce_read = "workforce:read"


_ALL_READ = {
    Permission.masters_read,
    Permission.styles_read,
    Permission.sales_read,
    Permission.procurement_read,
    Permission.inventory_read,
    Permission.production_read,
    Permission.quality_read,
    Permission.costing_read,
    Permission.finance_read,
    Permission.planning_read,
}

ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.admin: {*Permission},
    Role.readonly: _ALL_READ,
    Role.merchandiser: {
        Permission.masters_read,
        Permission.styles_read,
        Permission.sales_read,
        Permission.procurement_read,
        Permission.inventory_read,
        Permission.production_read,
        Permission.quality_read,
        Permission.costing_read,
        Permission.planning_read,
    },
    Role.procurement: {
        Permission.masters_read,
        Permission.sales_read,
        Permission.procurement_read,
        Permission.inventory_read,
        Permission.production_read,
        Permission.quality_read,
        Permission.planning_read,
    },
    Role.stores: {
        Permission.masters_read,
        Permission.procurement_read,
        Permission.inventory_read,
        Permission.production_read,
        Permission.quality_read,
    },
    Role.cutting_supervisor: {
        Permission.masters_read,
        Permission.inventory_read,
        Permission.production_read,
        Permission.quality_read,
        Permission.workforce_read,
    },
    Role.sewing_supervisor: {
        Permission.masters_read,
        Permission.production_read,
        Permission.quality_read,
        Permission.workforce_read,
    },
    Role.quality_inspector: {
        Permission.masters_read,
        Permission.styles_read,
        Permission.sales_read,
        Permission.inventory_read,
        Permission.production_read,
        Permission.quality_read,
    },
    Role.planner: {
        Permission.masters_read,
        Permission.styles_read,
        Permission.sales_read,
        Permission.procurement_read,
        Permission.inventory_read,
        Permission.production_read,
        Permission.quality_read,
        Permission.costing_read,
        Permission.planning_read,
        Permission.workforce_read,
    },
    Role.finance: _ALL_READ | {Permission.workforce_read},
}


@dataclass(frozen=True)
class Principal:
    user_id: int
    email: str
    display_name: str
    roles: frozenset[Role]
    session_id: int
    must_change_password: bool = False

    @property
    def permissions(self) -> frozenset[Permission]:
        return frozenset(
            permission
            for role in self.roles
            for permission in ROLE_PERMISSIONS.get(role, set())
        )


def _not_authenticated() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Session"},
    )


def get_current_principal(
    request: Request, session: Session = Depends(get_session)
) -> Principal:
    # Cookie is the browser path. An opaque Bearer value is also accepted for
    # trusted non-browser clients; both resolve to the same revocable DB session.
    token = request.cookies.get(settings.session_cookie_name)
    authorization = request.headers.get("Authorization", "")
    if not token and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token:
        raise _not_authenticated()

    from ..security.service import get_roles, resolve_session

    resolved = resolve_session(session, token)
    if resolved is None:
        raise _not_authenticated()
    user, auth_session = resolved
    roles = get_roles(session, user.id)
    if not roles:
        raise HTTPException(status_code=403, detail="No roles are assigned to this account")
    principal = Principal(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        roles=frozenset(roles),
        session_id=auth_session.id,
        must_change_password=user.must_change_password,
    )
    request.state.principal = principal
    return principal


def _require_current_password(principal: Principal) -> None:
    if principal.must_change_password:
        raise HTTPException(status_code=403, detail="Password change required")


def require_roles(*allowed: Role):
    allowed_set = set(allowed)

    def dependency(
        principal: Principal = Depends(get_current_principal),
        session: Session = Depends(get_session),
    ) -> str:
        _require_current_password(principal)
        if Role.admin not in principal.roles and principal.roles.isdisjoint(allowed_set):
            from ..security.service import audit

            audit(
                session,
                "authorization_denied",
                actor_user_id=principal.user_id,
                detail="required_role=" + ",".join(sorted(role.value for role in allowed_set)),
            )
            session.commit()
            raise HTTPException(status_code=403, detail="You do not have the required role")
        # Persist a stable user identity in operational audit fields, never a
        # caller-controlled role header.
        return principal.email

    return dependency


def require_permissions(*required: Permission):
    required_set = set(required)

    def dependency(
        principal: Principal = Depends(get_current_principal),
        session: Session = Depends(get_session),
    ) -> Principal:
        _require_current_password(principal)
        if not required_set.issubset(principal.permissions):
            from ..security.service import audit

            audit(
                session,
                "authorization_denied",
                actor_user_id=principal.user_id,
                detail="required_permission=" + ",".join(sorted(p.value for p in required_set)),
            )
            session.commit()
            raise HTTPException(status_code=403, detail="You do not have the required permission")
        return principal

    return dependency
