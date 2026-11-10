"""Notification test fixtures. Runs against a real, migrated ``notification_test`` database.

The queue is swapped for one that records ids, and the senders for fakes, so
tests drive "consume an event" and "send" as separate, observable steps.
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
        "DATABASE_URL",
        "postgresql+asyncpg://notification:notification@localhost:5447/notification",
    )
)
os.environ.update(
    DATABASE_URL=TEST_URL, ENVIRONMENT="test", JWT_PUBLIC_KEY=PUBLIC_PEM, LOG_JSON="false"
)
os.environ.pop("JWKS_URL", None)

import httpx  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

from notification import queue  # noqa: E402
from notification.channels import SendError  # noqa: E402
from notification.db import database  # noqa: E402
from notification.events import build_processor  # noqa: E402
from notification.main import app  # noqa: E402
from notification.models import Channel  # noqa: E402
from sl_platform.events import EventEnvelope  # noqa: E402
from sl_platform.roles import UserRole  # noqa: E402
from sl_platform.testing import mint_token  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def _schema() -> None:
    recreate_database(TEST_URL)
    migrate(ROOT / "alembic.ini", TEST_URL)


class RecordingQueue:
    def __init__(self) -> None:
        self.ids: list[uuid.UUID] = []

    async def enqueue(self, ids) -> None:
        self.ids.extend(ids)


@pytest.fixture(autouse=True)
def recorded() -> RecordingQueue:
    recording = RecordingQueue()
    queue.Holder.queue = recording
    return recording


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
        transport=httpx.ASGITransport(app=app), base_url="http://notification"
    ) as http:
        yield http


def auth(role: UserRole = UserRole.ADMIN, **claims) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_token(PRIVATE_PEM, role=role, **claims)}"}


class FakeSender:
    """Accepts messages (or fails the first ``fail`` attempts) and keeps them."""

    def __init__(self, fail: int = 0) -> None:
        self.fail = fail
        self.sent: list = []

    async def send(self, notification) -> str:
        if self.fail > 0:
            self.fail -= 1
            raise SendError("provider timed out")
        self.sent.append(notification)
        return f"fake-{len(self.sent)}"


@pytest.fixture
def senders() -> dict[Channel, FakeSender]:
    return {channel: FakeSender() for channel in Channel}


# --- events as the producers publish them --------------------------------------------

processor = build_processor(database.sessionmaker)


def envelope(event_type: str, payload: dict, key: str | None = None) -> EventEnvelope:
    return EventEnvelope(
        event_type=event_type, producer="test", key=key or str(payload.get("id")), payload=payload
    )


def shipment_payload(shipment_id: uuid.UUID, status: str = "created", **extra) -> dict:
    return {
        "id": str(shipment_id),
        "reference_no": f"SL-2026-{str(shipment_id)[:6].upper()}",
        "customer_reference": None,
        "status": status,
        "service_level": "express",
        "priority": "normal",
        "origin_warehouse_id": str(uuid.uuid4()),
        "origin_warehouse_code": "KHI-01",
        "courier_id": None,
        "total_weight_g": 1300,
        "promised_delivery_at": None,
        "version": 1,
        **extra,
    }


async def created(
    shipment_id: uuid.UUID,
    *,
    name: str = "Ayesha Khan",
    email: str | None = "ayesha@example.com",
    phone: str = "+923001234567",
) -> EventEnvelope:
    event = envelope(
        "shipment.created",
        shipment_payload(
            shipment_id,
            created_by=str(uuid.uuid4()),
            destination_city="Karachi",
            items=[{"sku_id": str(uuid.uuid4()), "quantity": 1}],
            recipient={"name": name, "phone": phone, "email": email},
        ),
    )
    await processor.process(event)
    return event


async def status_changed(
    shipment_id: uuid.UUID, status: str, previous: str, reason: str | None = None
) -> EventEnvelope:
    event = envelope(
        "shipment.status_changed",
        shipment_payload(shipment_id, status, previous_status=previous, reason=reason),
    )
    await processor.process(event)
    return event


def courier_payload(courier_id: uuid.UUID, **extra) -> dict:
    return {
        "id": str(courier_id),
        "code": "CR-0001",
        "user_id": None,
        "full_name": "Ali Hassan",
        "phone": "+923001110007",
        "email": "courier.one@transfleet.com",
        "vehicle_type": "motorcycle",
        "home_city": "Karachi",
        "home_warehouse_id": None,
        "status": "active",
        "availability": "available",
        "max_active_shipments": 8,
        "active_assignments": 0,
        "deleted": False,
        "updated_at": datetime.now(UTC).isoformat(),
        **extra,
    }


def assignment_payload(shipment_id: uuid.UUID, courier_id: uuid.UUID) -> dict:
    return {
        "assignment_id": str(uuid.uuid4()),
        "shipment_id": str(shipment_id),
        "courier_id": str(courier_id),
        "warehouse_id": str(uuid.uuid4()),
        "status": "active",
        "method": "automatic",
        "assigned_at": datetime.now(UTC).isoformat(),
        "ended_at": None,
        "end_reason": None,
    }
