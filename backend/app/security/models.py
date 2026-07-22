from datetime import datetime
from typing import Optional

from pydantic import ConfigDict, field_validator
from sqlalchemy import CheckConstraint
from sqlmodel import Field, SQLModel

from ..kernel.audit import utcnow
from .passwords import validate_password


class User(SQLModel, table=True):
    __tablename__ = "app_user"

    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True, max_length=320)
    display_name: str = Field(max_length=120)
    password_hash: str = Field(max_length=512)
    must_change_password: bool = Field(default=True, index=True)
    is_active: bool = Field(default=True, index=True)
    failed_login_attempts: int = Field(default=0)
    locked_until: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=utcnow, nullable=False)


class UserRole(SQLModel, table=True):
    __tablename__ = "user_role"
    __table_args__ = (
        CheckConstraint(
            "role IN ('admin','merchandiser','procurement','stores',"
            "'cutting_supervisor','sewing_supervisor','quality_inspector',"
            "'finance','planner','readonly')",
            name="ck_user_role_valid_role",
        ),
    )

    user_id: int = Field(foreign_key="app_user.id", primary_key=True, ondelete="CASCADE")
    role: str = Field(primary_key=True, max_length=64)
    granted_at: datetime = Field(default_factory=utcnow, nullable=False)
    granted_by: Optional[int] = Field(default=None, foreign_key="app_user.id")


class AuthSession(SQLModel, table=True):
    __tablename__ = "auth_session"

    id: Optional[int] = Field(default=None, primary_key=True)
    token_hash: str = Field(index=True, unique=True, max_length=64)
    user_id: int = Field(foreign_key="app_user.id", index=True, ondelete="CASCADE")
    created_at: datetime = Field(default_factory=utcnow, nullable=False)
    last_seen_at: datetime = Field(default_factory=utcnow, index=True, nullable=False)
    expires_at: datetime = Field(index=True, nullable=False)
    revoked_at: Optional[datetime] = Field(default=None, index=True)
    ip_address: Optional[str] = Field(default=None, max_length=64)
    user_agent: Optional[str] = Field(default=None, max_length=512)


class SecurityAuditEvent(SQLModel, table=True):
    __tablename__ = "security_audit_event"

    id: Optional[int] = Field(default=None, primary_key=True)
    occurred_at: datetime = Field(default_factory=utcnow, index=True, nullable=False)
    event_type: str = Field(index=True, max_length=64)
    actor_user_id: Optional[int] = Field(default=None, foreign_key="app_user.id", index=True)
    target_user_id: Optional[int] = Field(default=None, foreign_key="app_user.id", index=True)
    email: Optional[str] = Field(default=None, max_length=320)
    ip_address: Optional[str] = Field(default=None, max_length=64)
    detail: Optional[str] = Field(default=None, max_length=1000)


class LoginThrottle(SQLModel, table=True):
    __tablename__ = "login_throttle"

    key_hash: str = Field(primary_key=True, max_length=64)
    window_started_at: datetime = Field(default_factory=utcnow, nullable=False)
    attempts: int = Field(default=0, nullable=False)


class LoginRequest(SQLModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not value or "@" not in value or len(value) > 320:
            raise ValueError("Enter a valid email address")
        return value


class UserCreate(SQLModel):
    email: str
    display_name: str
    password: str
    roles: list[str]

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not value or "@" not in value or len(value) > 320:
            raise ValueError("Enter a valid email address")
        return value

    @field_validator("password")
    @classmethod
    def strong_password(cls, value: str) -> str:
        return validate_password(value)

    @field_validator("display_name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 120:
            raise ValueError("Display name is required")
        return value


class UserUpdate(SQLModel):
    display_name: Optional[str] = None
    is_active: Optional[bool] = None
    roles: Optional[list[str]] = None
    password: Optional[str] = None

    @field_validator("password")
    @classmethod
    def strong_password(cls, value: Optional[str]) -> Optional[str]:
        return validate_password(value) if value is not None else None

    @field_validator("display_name")
    @classmethod
    def valid_name(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and (not value.strip() or len(value.strip()) > 120):
            raise ValueError("Display name must be between 1 and 120 characters")
        return value.strip() if value is not None else None


class UserRead(SQLModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    display_name: str
    is_active: bool
    must_change_password: bool
    roles: list[str]
    permissions: list[str] = Field(default_factory=list)
    created_at: datetime


class ChangePasswordRequest(SQLModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def strong_password(cls, value: str) -> str:
        return validate_password(value)
