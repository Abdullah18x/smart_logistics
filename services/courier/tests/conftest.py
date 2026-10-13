"""Courier test fixtures. Runs against a real, migrated ``courier_test`` database."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sl_platform.testing import generate_rsa_keypair, migrate, recreate_database, test_database_url

PRIVATE_PEM, PUBLIC_PEM = generate_rsa_keypair()
TEST_URL = test_database_url(
    os.environ.get("DATABASE_URL", "postgresql+asyncpg://courier:courier@localhost:5445/courier")
)
os.environ.update(
    DATABASE_URL=TEST_URL, ENVIRONMENT="test", JWT_PUBLIC_KEY=PUBLIC_PEM, LOG_JSON="false"
)
os.environ.pop("JWKS_URL", None)

import httpx  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

from courier.db import database  # noqa: E402
from courier.main import app  # noqa: E402
from courier.models import Availability, Courier, CourierStatus, VehicleType  # noqa: E402
from courier.services.replicas import upsert_warehouse  # noqa: E402
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
        transport=httpx.ASGITransport(app=app), base_url="http://courier"
    ) as http:
        yield http


def auth(role: UserRole = UserRole.ADMIN, **claims) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_token(PRIVATE_PEM, role=role, **claims)}"}


def system() -> dict[str, str]:
    return auth(UserRole.SYSTEM)


def as_courier(courier_id: uuid.UUID) -> dict[str, str]:
    return auth(UserRole.COURIER, courier_id=courier_id)


class Fleet:
    """Builds warehouses (in the replica) and couriers straight in the database."""

    _codes = 0

    async def warehouse(self, city: str = "Karachi", *, status: str = "active") -> uuid.UUID:
        warehouse_id = uuid.uuid4()
        async with database.scope() as session:
            await upsert_warehouse(
                session,
                {
                    "id": warehouse_id,
                    "code": f"W-{str(warehouse_id)[:4].upper()}",
                    "name": f"{city} hub",
                    "status": status,
                    "city": city,
                    "deleted": False,
                    "updated_at": datetime.now(UTC).isoformat(),
                },
            )
        return warehouse_id

    async def courier(
        self,
        city: str = "Karachi",
        *,
        capacity: int = 5,
        load: int = 0,
        availability: Availability = Availability.AVAILABLE,
        status: CourierStatus = CourierStatus.ACTIVE,
        home_warehouse_id: uuid.UUID | None = None,
        max_weight_g: int | None = None,
        user_id: uuid.UUID | None = None,
    ) -> Courier:
        Fleet._codes += 1
        courier = Courier(
            id=uuid.uuid4(),
            code=f"CR-T{Fleet._codes:04d}",
            user_id=user_id,
            full_name=f"Courier {Fleet._codes}",
            phone="+923001234567",
            vehicle_type=VehicleType.MOTORCYCLE,
            max_active_shipments=capacity,
            active_assignments=load,
            max_weight_g=max_weight_g,
            home_city=city,
            home_warehouse_id=home_warehouse_id,
            status=status,
            availability=availability,
        )
        async with database.scope() as session:
            session.add(courier)
        return courier

    async def reload(self, courier_id: uuid.UUID) -> Courier:
        async with database.scope() as session:
            return await session.get(Courier, courier_id)


@pytest.fixture
def fleet() -> Fleet:
    return Fleet()
