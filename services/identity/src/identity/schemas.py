"""Request and response contracts for Identity."""

import re
import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, field_validator

from identity.models import UserStatus
from sl_platform.roles import UserRole

PHONE_PATTERN = r"^\+?[0-9\s\-()]{7,32}$"
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
    Field(min_length=PASSWORD_MIN_LENGTH, max_length=128),
    AfterValidator(validate_password_strength),
]


def _normalise_email(value: str) -> str:
    return value.strip().lower()


# --- users --------------------------------------------------------------------


class UserBase(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=200)
    phone: str | None = Field(default=None, max_length=32, pattern=PHONE_PATTERN)

    @field_validator("email")
    @classmethod
    def normalise_email(cls, value: str) -> str:
        return _normalise_email(value)

    @field_validator("full_name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return " ".join(value.split())


class UserCreate(UserBase):
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
    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    phone: str | None = Field(default=None, max_length=32, pattern=PHONE_PATTERN)


class UserRoleUpdate(BaseModel):
    role: UserRole
    reason: str | None = Field(default=None, max_length=500)


class UserStatusUpdate(BaseModel):
    status: UserStatus
    reason: str | None = Field(default=None, max_length=500)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: StrongPassword


class UserRead(UserBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: UserRole
    status: UserStatus
    courier_id: uuid.UUID | None = None
    last_login_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class WarehouseAssignmentCreate(BaseModel):
    warehouse_id: uuid.UUID


class WarehouseAssignmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    warehouse_id: uuid.UUID
    created_at: datetime


# --- auth ---------------------------------------------------------------------


class LoginRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {"email": "admin@transfleet.com", "password": "SmartLogistics!2026"}
        }
    )

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def normalise_email(cls, value: str) -> str:
        return _normalise_email(value)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Access token lifetime in seconds.")


class LoginResponse(TokenPair):
    user: UserRead


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class LogoutRequest(BaseModel):
    refresh_token: str | None = Field(
        default=None, description="Omit to revoke every session for the current user."
    )


class SessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    expires_at: datetime
    user_agent: str | None = None
    ip_address: str | None = None
