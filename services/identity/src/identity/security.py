"""Password hashing (Argon2id) and opaque-token hashing (SHA-256)."""

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_hasher = PasswordHasher()

#: A real Argon2id hash of a random value, verified against when the email is
#: unknown so response time does not reveal whether an account exists.
DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(plain: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, plain)
    except (VerifyMismatchError, InvalidHashError):
        return False


def generate_token(length: int = 48) -> str:
    return secrets.token_urlsafe(length)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
