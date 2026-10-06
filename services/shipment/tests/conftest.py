"""Shipment test fixtures. Runs against a real, migrated ``shipment_test`` database."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from sl_platform.testing import generate_rsa_keypair, migrate, recreate_database, test_database_url

PRIVATE_PEM, PUBLIC_PEM = generate_rsa_keypair()
TEST_URL = test_database_url(
    os.environ.get("DATABASE_URL", "postgresql+asyncpg://shipment:shipment@localhost:5432/shipment")
)
# Tokens are verified with a static public key here; in a deployment the
# service fetches it from Identity's JWKS endpoint instead.
os.environ.update(
    DATABASE_URL=TEST_URL, ENVIRONMENT="test", JWT_PUBLIC_KEY=PUBLIC_PEM, LOG_JSON="false"
)
os.environ.pop("JWKS_URL", None)

import httpx  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

from shipment.db import database  # noqa: E402
from shipment.main import app  # noqa: E402
from sl_platform.roles import UserRole  # noqa: E402
from sl_platform.testing import mint_token  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def _schema() -> None:
    recreate_database(TEST_URL)
    migrate(ROOT / "alembic.ini", TEST_URL)


@pytest.fixture(autouse=True)
async def _clean():
    yield
    async with database.engine.begin() as connection:
        tables = (
            (
                await connection.execute(
                    text(
                        "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                        "AND tablename <> 'alembic_version'"
                    )
                )
            )
            .scalars()
            .all()
        )
        await connection.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))


@pytest.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://shipment"
    ) as http:
        yield http


def auth(role: UserRole = UserRole.ADMIN, **claims) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_token(PRIVATE_PEM, role=role, **claims)}"}


def operator(*warehouse_ids: uuid.UUID) -> dict[str, str]:
    return auth(UserRole.WAREHOUSE_OPERATOR, warehouse_ids=tuple(warehouse_ids))


@pytest.fixture
def factory():
    from tests.factories import Factory

    return Factory(database.sessionmaker)


@pytest.fixture
def inventory():
    from shipment.api.deps import get_inventory
    from tests.factories import FakeInventory

    fake = FakeInventory()
    app.dependency_overrides[get_inventory] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_inventory, None)
