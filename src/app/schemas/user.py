"""Pydantic contracts for user management endpoints."""

import re
import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.constants.enums import UserRole, UserStatus
from app.constants.formats import PHONE_PATTERN

PASSWORD_MIN_LENGTH = 12
_PASSWORD_RULES = (
    (re.compile(r"[a-z]"), "a lowercase letter"),
    (re.compile(r"[A-Z]"), "an uppercase letter"),
    (re.compile(r"\d"), "a digit"),
    (re.compile(r"[^A-Za-z0-9]"), "a symbol"),
)


def validate_password_strength(value: str) -> str:
    missing = [label for pattern, label in _PASSWORD_RULES if not pattern.search(value)]
    if missing:
        raise ValueError("password must contain " + ", ".join(missing))
    return value


StrongPassword = Annotated[
    str,
    Field(
        min_length=PASSWORD_MIN_LENGTH,
        max_length=128,
        description=(
            f"At least {PASSWORD_MIN_LENGTH} characters, with lower case, upper case, "
            "a digit and a symbol."
        ),
    ),
    AfterValidator(validate_password_strength),
]


class UserBase(BaseModel):
    """Fields a client may supply for a user in any direction."""

    email: EmailStr = Field(description="Login identifier. Stored lowercased.")
    full_name: str = Field(min_length=2, max_length=200)
    phone: str | None = Field(
        default=None,
        max_length=32,
        pattern=PHONE_PATTERN,
        description="E.164 preferred. Used for courier and support contact.",
    )

    @field_validator("email")
    @classmethod
    def normalise_email(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("full_name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return " ".join(value.split())


class UserCreate(UserBase):
    """Admin-driven user registration."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": "ops.lead@transfleet.com",
                "full_name": "Ayesha Khan",
                "phone": "+923001234567",
                "password": "Dispatch!2026Ops",
                "role": "warehouse_operator",
            }
        }
    )

    password: StrongPassword
    role: UserRole


class UserUpdate(BaseModel):
    """Self-service profile update. Role and status are deliberately absent."""

    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    phone: str | None = Field(default=None, max_length=32, pattern=PHONE_PATTERN)


class UserRoleUpdate(BaseModel):
    """Privilege change — admin only, always audited."""

    role: UserRole
    reason: str | None = Field(default=None, max_length=500)


class UserStatusUpdate(BaseModel):
    """Suspend, reactivate or deactivate an account — admin only, always audited."""

    status: UserStatus
    reason: str | None = Field(default=None, max_length=500)


class PasswordChange(BaseModel):
    """Authenticated password change. Revokes all other sessions on success."""

    current_password: str = Field(min_length=1, max_length=128)
    new_password: StrongPassword


class UserRead(UserBase):
    """Full representation returned to admins and to the user themselves."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: UserRole
    status: UserStatus
    last_login_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class UserSummary(BaseModel):
    """Compact reference embedded in other resources, e.g. audit entries."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str
    role: UserRole


class UserFilter(BaseModel):
    """Query parameters for listing users."""

    role: UserRole | None = None
    status: UserStatus | None = None
    search: str | None = Field(
        default=None, max_length=200, description="Matches name or email, case-insensitive."
    )


class WarehouseAssignmentCreate(BaseModel):
    """Grants a warehouse operator access to a warehouse's rows."""

    warehouse_id: uuid.UUID


class WarehouseAssignmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    warehouse_id: uuid.UUID
    created_at: datetime
