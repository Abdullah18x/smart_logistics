"""Analytics test fixtures. Runs against a real, migrated ``analytics_test`` database.

Events are fed straight to the processor with chosen ``occurred_at`` times, so
day boundaries and durations are exact.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sl_platform.testing import generate_rsa_keypair, migrate, recreate_database, test_database_url

PRIVATE_PEM, PUBLIC_PEM = generate_rsa_keypair()
TEST_URL = test_database_url(
    os.environ.get(
        "DATABASE_URL", "postgresql+asyncpg://analytics:analytics@localhost:5446/analytics"
    )
)
os.environ.update(
    DATABASE_URL=TEST_URL, ENVIRONMENT="test", JWT_PUBLIC_KEY=PUBLIC_PEM, LOG_JSON="false"
)
os.environ.pop("JWKS_URL", None)

import httpx  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

from analytics import jobs  # noqa: E402
from analytics.db import database  # noqa: E402
from analytics.events import build_processor  # noqa: E402
from analytics.main import app  # noqa: E402
from sl_platform.events import EventEnvelope  # noqa: E402
from sl_platform.roles import UserRole  # noqa: E402
from sl_platform.testing import mint_token  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
KHI = uuid.UUID("00000000-0000-0000-0000-00000000a001")
LHE = uuid.UUID("00000000-0000-0000-0000-00000000a002")
CODES = {KHI: "KHI-01", LHE: "LHE-01"}


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
        transport=httpx.ASGITransport(app=app), base_url="http://analytics"
    ) as http:
        yield http


@pytest.fixture
def queued(monkeypatch) -> list:
    calls: list = []

    async def record(start, end):
        calls.append((start, end))

    monkeypatch.setattr(jobs.Holder, "queue_recompute", staticmethod(record))
    return calls


def auth(role: UserRole = UserRole.ADMIN, **claims) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_token(PRIVATE_PEM, role=role, **claims)}"}


def at(day: int, hour: int = 9, month: int = 11) -> datetime:
    return datetime(2026, month, day, hour, tzinfo=UTC)


processor = build_processor(database.sessionmaker)


async def feed(
    event_type: str, payload: dict, occurred_at: datetime, event_id: uuid.UUID | None = None
) -> EventEnvelope:
    event = EventEnvelope(
        event_id=event_id or uuid.uuid4(),
        event_type=event_type,
        producer="test",
        key=str(payload.get("id") or payload.get("shipment_id")),
        payload=payload,
        occurred_at=occurred_at,
    )
    await processor.process(event)
    return event


def shipment(
    shipment_id: uuid.UUID,
    status: str,
    version: int,
    *,
    warehouse: uuid.UUID = KHI,
    courier_id: uuid.UUID | None = None,
    reason: str | None = None,
) -> dict:
    return {
        "id": str(shipment_id),
        "reference_no": f"SL-2026-{str(shipment_id)[:6].upper()}",
        "status": status,
        "service_level": "express",
        "priority": "normal",
        "origin_warehouse_id": str(warehouse),
        "origin_warehouse_code": CODES[warehouse],
        "courier_id": str(courier_id) if courier_id else None,
        "total_weight_g": 1000,
        "version": version,
        "reason": reason,
    }


async def journey(
    *steps: tuple[str, datetime],
    warehouse: uuid.UUID = KHI,
    courier_id: uuid.UUID | None = None,
    shipment_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Feed a shipment's events in order: ('created', t0), ('dispatched', t1), ..."""
    shipment_id = shipment_id or uuid.uuid4()
    for version, (status, when) in enumerate(steps, start=1):
        payload = shipment(shipment_id, status, version, warehouse=warehouse,
                           courier_id=courier_id if status != "created" else None)  # fmt: skip
        if status == "created":
            await feed("shipment.created", payload, when)
        elif status == "assigned":
            await feed("courier.assigned", assignment(shipment_id, courier_id, warehouse), when)
        else:
            await feed("shipment.status_changed", payload, when)
    return shipment_id


def assignment(shipment_id: uuid.UUID, courier_id: uuid.UUID, warehouse: uuid.UUID) -> dict:
    return {
        "assignment_id": str(uuid.uuid4()),
        "shipment_id": str(shipment_id),
        "courier_id": str(courier_id),
        "warehouse_id": str(warehouse),
        "status": "active",
        "method": "automatic",
        "assigned_at": at(1).isoformat(),
        "ended_at": None,
        "end_reason": None,
    }


def courier(
    courier_id: uuid.UUID,
    *,
    active: int = 0,
    capacity: int = 8,
    warehouse: uuid.UUID = KHI,
    updated_at: datetime | None = None,
    status: str = "active",
    code: str = "CR-0001",
) -> dict:
    return {
        "id": str(courier_id),
        "code": code,
        "user_id": None,
        "full_name": "Ali Hassan",
        "phone": "+923001110007",
        "email": None,
        "vehicle_type": "motorcycle",
        "home_city": "Karachi",
        "home_warehouse_id": str(warehouse),
        "status": status,
        "availability": "available",
        "max_active_shipments": capacity,
        "active_assignments": active,
        "deleted": False,
        "updated_at": (updated_at or at(1)).isoformat(),
    }
