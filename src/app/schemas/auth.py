"""Pydantic contracts for authentication and session management."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.enums import TokenType, UserRole
from app.schemas.user import StrongPassword, UserRead


class LoginRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            # Seeded admin credentials, so the example works as-is.
            "example": {
                "email": "admin@transfleet.com",
                "password": "SmartLogistics!2026",
            }
        }
    )

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def normalise_email(cls, value: str) -> str:
        return value.strip().lower()


class TokenPair(BaseModel):
    """Issued on login and on refresh. Refresh tokens rotate on every use."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Access token lifetime in seconds.")


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class LogoutRequest(BaseModel):
    refresh_token: str | None = Field(
        default=None, description="Omit to revoke every session for the current user."
    )


class TokenPayload(BaseModel):
    """Decoded JWT claims. Not a request or response body."""

    sub: uuid.UUID = Field(description="User id.")
    role: UserRole
    type: TokenType
    jti: uuid.UUID = Field(description="Token id, used for revocation checks.")
    iat: datetime
    exp: datetime


class LoginResponse(TokenPair):
    """Tokens plus the profile, so the client avoids an immediate second call."""

    user: UserRead


class PasswordResetRequest(BaseModel):
    """Always answered with 202 regardless of whether the email exists."""

    email: EmailStr

    @field_validator("email")
    @classmethod
    def normalise_email(cls, value: str) -> str:
        return value.strip().lower()


class PasswordResetConfirm(BaseModel):
    token: str = Field(min_length=1)
    new_password: StrongPassword


class SessionRead(BaseModel):
    """An active refresh-token session, for the 'where am I logged in' view."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    expires_at: datetime
    user_agent: str | None = None
    ip_address: str | None = None
