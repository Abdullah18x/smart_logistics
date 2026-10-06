"""Test helpers shared by every service's suite. Never imported at runtime."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy.engine import make_url

from sl_platform.roles import UserRole

TEST_ISSUER = "smartlogistics-identity"
TEST_AUDIENCE = "smartlogistics"
TEST_KID = "test-key"


def generate_rsa_keypair() -> tuple[str, str]:
    """Return (private PEM, public PEM)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return private_pem, public_pem


def mint_token(
    private_pem: str,
    *,
    user_id: uuid.UUID | None = None,
    role: UserRole = UserRole.ADMIN,
    warehouse_ids: tuple[uuid.UUID, ...] = (),
    courier_id: uuid.UUID | None = None,
    ttl: timedelta = timedelta(minutes=15),
    token_type: str = "access",
    issuer: str = TEST_ISSUER,
    audience: str = TEST_AUDIENCE,
) -> str:
    """An access token shaped exactly like Identity's, for other services' tests."""
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id or uuid.uuid4()),
        "role": role.value,
        "type": token_type,
        "warehouse_ids": [str(w) for w in warehouse_ids],
        "courier_id": str(courier_id) if courier_id else None,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + ttl,
        "iss": issuer,
        "aud": audience,
    }
    return jwt.encode(payload, private_pem, algorithm="RS256", headers={"kid": TEST_KID})


def test_database_url(url: str) -> str:
    """Append ``_test`` to the database name, refusing to run against anything else."""
    parsed = make_url(url)
    name = parsed.database or "postgres"
    if not name.endswith("_test"):
        name = f"{name}_test"
    return parsed.set(database=name).render_as_string(hide_password=False)


def recreate_database(url: str) -> None:
    """Drop and create the (``_test``) database named in ``url``."""
    import asyncpg

    parsed = make_url(url)
    name = parsed.database
    assert name and name.endswith("_test"), "refusing to drop a non-test database"

    async def _run() -> None:
        connection = await asyncpg.connect(
            user=parsed.username,
            password=parsed.password,
            host=parsed.host or "localhost",
            port=parsed.port or 5432,
            database="postgres",
        )
        try:
            await connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            await connection.execute(f'CREATE DATABASE "{name}"')
        finally:
            await connection.close()

    asyncio.run(_run())


def migrate(alembic_ini: Path, url: str) -> None:
    """Build the schema from the service's migrations, as production would."""
    from alembic import command
    from alembic.config import Config

    config = Config(str(alembic_ini))
    config.set_main_option("script_location", str(alembic_ini.parent / "migrations"))
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
