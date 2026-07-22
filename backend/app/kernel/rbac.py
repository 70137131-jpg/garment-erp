from enum import Enum

from fastapi import Header, HTTPException


class Role(str, Enum):
    """Roles from blueprint 9.1. Enforcement is a thin dev stub for now:
    the acting role is read from an ``X-Role`` header. Real auth (users,
    sessions, segregation of duties) replaces this in the hardening phase,
    but attaching ``require_roles`` to endpoints as we build means the
    permission surface is never a retrofit.
    """

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


def require_roles(*allowed: Role):
    allowed_set = {r.value for r in allowed}

    def dependency(x_role: str = Header(default="admin")):
        if x_role == Role.admin.value:
            return x_role
        if allowed_set and x_role not in allowed_set:
            raise HTTPException(
                status_code=403,
                detail=f"Role '{x_role}' is not permitted for this action",
            )
        return x_role

    return dependency
