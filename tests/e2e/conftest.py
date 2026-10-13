"""Every service in one process, each on its own store, joined by an in-memory
event bus instead of Kafka and a real Temporal dev server for the workflows.

Proves the services cooperate only through HTTP and events — no shared tables,
no imports between services — and that dispatch and delivery work end to end:
the orchestrator's activities call the other services over (in-process) HTTP
with a service-account token issued by Identity.

Each service reads its settings when its modules are first imported, so the
harness sets the environment, imports one service, and moves to the next.

Needs: the PostgreSQL databases (``./dev.sh infra up``, or SL_PG_HOST for one
server), MongoDB on MONGO_URL (default localhost:27017), and the Temporal CLI
(downloaded by the SDK, or TEMPORAL_CLI_PATH).
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
from pathlib import Path

from sl_platform.testing import generate_rsa_keypair, migrate, recreate_database

ROOT = Path(__file__).resolve().parents[2]
PRIVATE_PEM, PUBLIC_PEM = generate_rsa_keypair()
#: Services with a PostgreSQL database.
SERVICES = ("identity", "warehouse", "inventory", "shipment", "courier")
#: Each service's Postgres as Docker Compose publishes it. Set SL_PG_HOST
#: (e.g. ``localhost:5432``) to put every database on one server instead.
PORTS = {
    "identity": 5441,
    "warehouse": 5442,
    "inventory": 5443,
    "shipment": 5444,
    "courier": 5445,
}
SERVICE_SECRET = "orchestrator-e2e-secret"


def url(service: str) -> str:
    host = os.environ.get("SL_PG_HOST") or f"localhost:{PORTS[service]}"
    return f"postgresql+asyncpg://{service}:{service}@{host}/{service}_e2e_test"


os.environ.update(
    ENVIRONMENT="test",
    LOG_JSON="false",
    JWT_KEY_ID="e2e",
    SERVICE_CLIENTS=json.dumps({"orchestrator": SERVICE_SECRET}),
    MONGO_DATABASE="tracking_e2e_test",
)
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
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
apps["tracking"] = importlib.import_module("tracking.main").app

import httpx  # noqa: E402
import pytest  # noqa: E402
from temporalio.testing import WorkflowEnvironment  # noqa: E402
from temporalio.worker import Worker  # noqa: E402

from courier.db import database as courier_db  # noqa: E402
from courier.events import build_processor as courier_processor  # noqa: E402
from identity.api.deps import get_warehouse_client  # noqa: E402
from identity.db import database as identity_db  # noqa: E402
from identity.events import build_processor as identity_processor  # noqa: E402
from inventory.db import database as inventory_db  # noqa: E402
from inventory.events import build_processor as inventory_processor  # noqa: E402
from orchestrator.activities import DispatchActivities  # noqa: E402
from orchestrator.config import Settings as OrchestratorSettings  # noqa: E402
from orchestrator.trigger import handle as orchestrator_handle  # noqa: E402
from orchestrator.workflows.delivery import DeliveryWorkflow  # noqa: E402
from orchestrator.workflows.dispatch import DispatchWorkflow  # noqa: E402
from shipment.api.deps import get_inventory  # noqa: E402
from shipment.clients import InventoryClient  # noqa: E402
from shipment.db import database as shipment_db  # noqa: E402
from shipment.events import build_processor as shipment_processor  # noqa: E402
from sl_platform.consumer import LocalEventBus, StatelessProcessor  # noqa: E402
from sl_platform.events import Topics  # noqa: E402
from sl_platform.http import ServiceClient  # noqa: E402
from sl_platform.outbox import OutboxRelay  # noqa: E402
from sl_platform.service_auth import ServiceTokenProvider  # noqa: E402
from tracking.api.deps import journeys as tracking_journeys  # noqa: E402
from tracking.api.deps import store as tracking_store  # noqa: E402
from tracking.events import build_processor as tracking_processor  # noqa: E402
from tracking.relay import TrackingRelay  # noqa: E402
from warehouse.db import database as warehouse_db  # noqa: E402

DATABASES = {
    "identity": identity_db,
    "warehouse": warehouse_db,
    "inventory": inventory_db,
    "shipment": shipment_db,
    "courier": courier_db,
}
ORCHESTRATOR = OrchestratorSettings(
    task_queue="e2e",
    courier_wait_attempts=2,
    courier_wait_seconds=0,
    pickup_timeout_seconds=3600,
    max_delivery_attempts=3,
)


@pytest.fixture(scope="session", autouse=True)
def _schemas() -> None:
    for service in SERVICES:
        recreate_database(url(service))
        migrate(ROOT / "services" / service / "alembic.ini", url(service))


def asgi(service: str) -> httpx.ASGITransport:
    return httpx.ASGITransport(app=apps[service])


class Platform:
    """Clients for each service, the event bus that stands in for Kafka, and
    the Temporal client the orchestrator uses."""

    def __init__(self, temporal_client) -> None:
        self.temporal = temporal_client
        self.http = {
            name: httpx.AsyncClient(transport=asgi(name), base_url=f"http://{name}")
            for name in apps
        }
        self.bus = LocalEventBus()
        for processor in (
            identity_processor(identity_db.sessionmaker),
            inventory_processor(inventory_db.sessionmaker),
            shipment_processor(shipment_db.sessionmaker),
            courier_processor(courier_db.sessionmaker),
            tracking_processor(tracking_journeys),
            self._orchestrator_trigger(),
        ):
            self.bus.subscribe(processor)
        self.relays = [OutboxRelay(db.sessionmaker, self.bus) for db in DATABASES.values()]
        self.tracking_relay = TrackingRelay(tracking_store, self.bus)

    def _orchestrator_trigger(self) -> StatelessProcessor:
        async def forward(envelope) -> None:
            await orchestrator_handle(self.temporal, ORCHESTRATOR, envelope)

        return StatelessProcessor(
            "orchestrator",
            handlers={
                "shipment.dispatch_requested": forward,
                "tracking.milestone_recorded": forward,
            },
            topics=(Topics.SHIPMENT, Topics.TRACKING),
        )

    async def pump(self) -> int:
        """Run every service's outbox relay until nothing is left to deliver.
        In production this is each service's worker publishing to Kafka."""
        total = 0
        while True:
            sent = sum([await relay.run_once() for relay in self.relays])
            sent += await self.tracking_relay.publish_batch()
            total += sent
            if not sent:
                return total

    async def settle(self, workflow_id: str):
        """Wait for a workflow while pumping events, as the workers would."""
        handle = self.temporal.get_workflow_handle(workflow_id)
        result_task = asyncio.ensure_future(handle.result())
        while not result_task.done():
            await self.pump()
            await asyncio.sleep(0.1)
        await self.pump()
        return result_task.result()

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
async def temporal():
    environment = await WorkflowEnvironment.start_local(
        dev_server_existing_path=os.environ.get("TEMPORAL_CLI_PATH"),
        dev_server_log_level="error",
    )
    yield environment
    await environment.shutdown()


@pytest.fixture(scope="session")
async def platform(temporal):
    from courier.seed import seed as seed_couriers
    from identity.seed import seed as seed_identity
    from inventory.seed import seed as seed_inventory
    from warehouse.seed import seed as seed_warehouses

    await tracking_store.client.drop_database("tracking_e2e_test")
    await tracking_store.ensure_indexes()

    p = Platform(temporal.client)
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
    await seed_couriers()
    await p.pump()  # warehouse.created -> replicas; courier.created -> identity link

    # The orchestrator's worker, calling the services as its own service account.
    tokens = ServiceTokenProvider(
        "http://identity", "orchestrator", SERVICE_SECRET, transport=asgi("identity")
    )

    def service(name: str) -> ServiceClient:
        return ServiceClient(
            name, f"http://{name}", transport=asgi(name), service_tokens=tokens, timeout=10
        )

    activities = DispatchActivities(
        shipment=service("shipment"),
        inventory=service("inventory"),
        courier=service("courier"),
        tracking=service("tracking"),
    )
    worker = Worker(
        temporal.client,
        task_queue=ORCHESTRATOR.task_queue,
        workflows=[DispatchWorkflow, DeliveryWorkflow],
        activities=activities.all(),
    )
    worker_task = asyncio.create_task(worker.run())
    yield p
    await worker.shutdown()
    worker_task.cancel()
    await p.aclose()
