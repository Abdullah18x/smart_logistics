"""Tracking test fixtures. Runs against a real MongoDB (MONGO_URL), test database."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

from sl_platform.testing import generate_rsa_keypair

PRIVATE_PEM, PUBLIC_PEM = generate_rsa_keypair()
os.environ.update(
    ENVIRONMENT="test",
    JWT_PUBLIC_KEY=PUBLIC_PEM,
    LOG_JSON="false",
    MONGO_DATABASE="tracking_test",
)
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.pop("JWKS_URL", None)

import httpx  # noqa: E402
import pytest  # noqa: E402

from sl_platform.roles import UserRole  # noqa: E402
from sl_platform.testing import mint_token  # noqa: E402
from tracking.api.deps import store  # noqa: E402
from tracking.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
async def _database():
    await store.client.drop_database("tracking_test")
    await store.ensure_indexes()
    yield


@pytest.fixture(autouse=True)
async def _clean():
    yield
    await store.journeys.delete_many({})
    await store.events.delete_many({})


@pytest.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://tracking"
    ) as http:
        yield http


def auth(role: UserRole = UserRole.ADMIN, **claims) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_token(PRIVATE_PEM, role=role, **claims)}"}


def system() -> dict[str, str]:
    return auth(UserRole.SYSTEM)


def as_courier(courier_id) -> dict[str, str]:
    return auth(UserRole.COURIER, courier_id=courier_id)


def at(offset: float) -> str:
    """A device timestamp `offset` seconds after now (well within the allowed skew)."""
    base = datetime.now(UTC) + timedelta(seconds=1)
    return (base + timedelta(seconds=offset)).isoformat()


async def open_journey(client, *, courier_id=None, warehouse_id=None) -> dict:
    shipment_id = uuid.uuid4()
    body = {
        "shipment_id": str(shipment_id),
        "reference_no": f"SL-2026-{str(shipment_id)[:6]}",
        "courier_id": str(courier_id or uuid.uuid4()),
        "origin_warehouse_id": str(warehouse_id or uuid.uuid4()),
        "destination_city": "Karachi",
    }
    response = await client.post("/api/v1/tracking/journeys", json=body, headers=system())
    assert response.status_code == 201, response.text
    return response.json()
