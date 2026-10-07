"""Identity test fixtures. Runs against a real, migrated ``identity_test`` database."""

from __future__ import annotations

import os
from pathlib import Path

from sl_platform.testing import generate_rsa_keypair, migrate, recreate_database, test_database_url

PRIVATE_PEM, PUBLIC_PEM = generate_rsa_keypair()
TEST_URL = test_database_url(
    os.environ.get("DATABASE_URL", "postgresql+asyncpg://identity:identity@localhost:5441/identity")
)
# Must be set before anything from ``identity`` is imported: settings and the
# engine are built at import time.
os.environ.update(
    DATABASE_URL=TEST_URL,
    ENVIRONMENT="test",
    JWT_PRIVATE_KEY=PRIVATE_PEM,
    JWT_KEY_ID="test-key",
    LOG_JSON="false",
)

import httpx  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

from identity.api.deps import get_warehouse_client  # noqa: E402
from identity.db import database  # noqa: E402
from identity.main import app  # noqa: E402
from tests.factories import Factory, FakeWarehouseClient  # noqa: E402

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
def warehouses() -> FakeWarehouseClient:
    fake = FakeWarehouseClient()
    app.dependency_overrides[get_warehouse_client] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_warehouse_client, None)


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://identity") as http:
        yield http


@pytest.fixture
def factory() -> Factory:
    return Factory(database.sessionmaker, PRIVATE_PEM)
