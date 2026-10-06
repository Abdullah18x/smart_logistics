"""All four services in one process, each on its own database, joined by an
in-memory event bus instead of Kafka. Proves the services cooperate only
through HTTP and events — no shared tables, no imports between services.

Each service reads ``DATABASE_URL`` when its modules are first imported, so
the harness sets the variable, imports one service, and moves to the next.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path

from sl_platform.testing import generate_rsa_keypair, migrate, recreate_database

ROOT = Path(__file__).resolve().parents[2]
PRIVATE_PEM, PUBLIC_PEM = generate_rsa_keypair()
SERVICES = ("identity", "warehouse", "inventory", "shipment")
HOST = os.environ.get("E2E_PG_HOST", "localhost:5432")


def url(service: str) -> str:
    return f"postgresql+asyncpg://{service}:{service}@{HOST}/{service}_e2e_test"


os.environ.update(ENVIRONMENT="test", LOG_JSON="false", JWT_KEY_ID="e2e")
os.environ.pop("JWKS_URL", None)
apps = {}
for service in SERVICES:
    os.environ["DATABASE_URL"] = url(service)
    if service == "identity":
        os.environ["JWT_PRIVATE_KEY"] = PRIVATE_PEM
        os.environ.pop("JWT_PUBLIC_KEY", None)
    else:
        os.environ["JWT_PUBLIC_KEY"] = PUBLIC_PEM
    apps[service] = importlib.import_module(f"{service}.main").app

import httpx  # noqa: E402
import pytest  # noqa: E402

from identity.api.deps import get_warehouse_client  # noqa: E402
from identity.db import database as identity_db  # noqa: E402
from identity.events import build_processor as identity_processor  # noqa: E402
from inventory.db import database as inventory_db  # noqa: E402
from inventory.events import build_processor as inventory_processor  # noqa: E402
from shipment.api.deps import get_inventory  # noqa: E402
from shipment.clients import InventoryClient  # noqa: E402
from shipment.db import database as shipment_db  # noqa: E402
from shipment.events import build_processor as shipment_processor  # noqa: E402
from sl_platform.consumer import LocalEventBus  # noqa: E402
from sl_platform.http import ServiceClient  # noqa: E402
from sl_platform.outbox import OutboxRelay  # noqa: E402
from warehouse.db import database as warehouse_db  # noqa: E402

DATABASES = {
    "identity": identity_db,
    "warehouse": warehouse_db,
    "inventory": inventory_db,
    "shipment": shipment_db,
}


@pytest.fixture(scope="session", autouse=True)
def _schemas() -> None:
    for service in SERVICES:
        recreate_database(url(service))
        migrate(ROOT / "services" / service / "alembic.ini", url(service))


def asgi(service: str) -> httpx.ASGITransport:
    return httpx.ASGITransport(app=apps[service])


class Platform:
    """Clients for each service plus the event bus that stands in for Kafka."""

    def __init__(self) -> None:
        self.http = {
            name: httpx.AsyncClient(transport=asgi(name), base_url=f"http://{name}")
            for name in SERVICES
        }
        self.bus = LocalEventBus()
        for processor in (
            identity_processor(identity_db.sessionmaker),
            inventory_processor(inventory_db.sessionmaker),
            shipment_processor(shipment_db.sessionmaker),
        ):
            self.bus.subscribe(processor)
        self.relays = [OutboxRelay(db.sessionmaker, self.bus) for db in DATABASES.values()]

    async def pump(self) -> int:
        """Run every service's outbox relay until nothing is left to deliver.
        In production this is each service's worker publishing to Kafka."""
        total = 0
        while True:
            sent = sum([await relay.run_once() for relay in self.relays])
            total += sent
            if not sent:
                return total

    async def login(self, email: str) -> dict[str, str]:
        response = await self.http["identity"].post(
            "/api/v1/auth/login", json={"email": email, "password": "SmartLogistics!2026"}
        )
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    async def aclose(self) -> None:
        for client in self.http.values():
            await client.aclose()


@pytest.fixture(scope="session")
async def platform():
    from identity.seed import seed as seed_identity
    from inventory.seed import seed as seed_inventory
    from warehouse.seed import seed as seed_warehouses

    p = Platform()
    # Real HTTP between services, routed in-process.
    apps["identity"].dependency_overrides[get_warehouse_client] = lambda: ServiceClient(
        "warehouse", "http://warehouse", transport=asgi("warehouse")
    )
    apps["shipment"].dependency_overrides[get_inventory] = lambda: InventoryClient(
        ServiceClient("inventory", "http://inventory", transport=asgi("inventory"))
    )
    await seed_identity()
    await seed_warehouses()
    await seed_inventory()
    await p.pump()  # warehouse.created -> Inventory and Shipment replicas
    yield p
    await p.aclose()
