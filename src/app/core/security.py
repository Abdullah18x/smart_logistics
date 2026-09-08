"""Password hashing primitives.

Argon2id is used for passwords (ADR-009). Tokens are hashed with SHA-256
instead: they are already high-entropy random values, so a slow KDF would add
latency on every refresh for no security gain.
"""

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(plain_password: str) -> str:
    return _hasher.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, plain_password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when the stored hash predates the current Argon2 parameters."""
    return _hasher.check_needs_rehash(password_hash)


def generate_token(length: int = 48) -> str:
    """A high-entropy, URL-safe token. Returned to the client once, never stored."""
    return secrets.token_urlsafe(length)


def hash_token(token: str) -> str:
    """Storage form for refresh and reset tokens."""
    return hashlib.sha256(token.encode()).hexdigest()
