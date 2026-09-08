"""JWT encoding and decoding.

Access tokens are stateless and short-lived. Refresh tokens are opaque random
strings stored hashed in the database (see ``core.security``), so they can be
revoked — a stateless refresh token could not be.
"""

import uuid
from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import settings
from app.core.enums import TokenType, UserRole
from app.core.exceptions import AuthenticationError


def create_access_token(user_id: uuid.UUID, role: UserRole) -> tuple[str, int]:
    """Return the encoded token and its lifetime in seconds."""
    now = datetime.now(UTC)
    expires_in = settings.access_token_ttl_minutes * 60
    payload = {
        "sub": str(user_id),
        "role": role.value,
        "type": TokenType.ACCESS.value,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
    }
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token, expires_in


def decode_access_token(token: str) -> dict:
    """Decode and validate an access token, or raise ``AuthenticationError``."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub", "jti"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Token has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Token is invalid.") from exc

    if payload.get("type") != TokenType.ACCESS.value:
        # A refresh token must never be accepted as a bearer credential.
        raise AuthenticationError("Token is not an access token.")
    return payload


def refresh_token_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(days=settings.refresh_token_ttl_days)
