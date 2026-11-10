"""The tracking store on MongoDB.

Two collections:

* ``journeys`` — one document per shipment (``_id`` = shipment id): the courier,
  the latest milestone by *occurred* time, attempt count and last position.
* ``tracking_events`` — every field update, append-only. ``_id`` is the
  device-generated ``event_id``, so a retried upload is rejected by the primary
  key itself: duplicates cost nothing and cannot double-count.

Each event document is also its own outbox entry (``published: false``): one
insert records the update *and* the intent to publish it, atomically, without
needing multi-document transactions. The worker's relay publishes and flips
the flag.
"""

from __future__ import annotations

from pymongo import ASCENDING, DESCENDING, AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from sl_platform.db import utcnow
from sl_platform.metrics import OUTBOX_OLDEST_AGE, OUTBOX_PENDING
from tracking.config import Settings


class TrackingStore:
    def __init__(self, settings: Settings) -> None:
        self.client: AsyncMongoClient = AsyncMongoClient(
            settings.mongo_url, tz_aware=True, uuidRepresentation="standard"
        )
        self.db: AsyncDatabase = self.client[settings.mongo_database]
        self.journeys = self.db["journeys"]
        self.events = self.db["tracking_events"]

    async def ensure_indexes(self) -> None:
        await self.events.create_index(
            [("shipment_id", ASCENDING), ("occurred_at", DESCENDING)], name="shipment_timeline"
        )
        await self.events.create_index(
            [("published", ASCENDING), ("received_at", ASCENDING)], name="outbox"
        )
        await self.journeys.create_index(
            [("courier_id", ASCENDING), ("status", ASCENDING)], name="courier_active"
        )
        await self.journeys.create_index("reference_no", name="reference_no", unique=True)

    async def ping(self) -> None:
        await self.db.command("ping")

    async def refresh_outbox_metrics(self) -> None:
        """The outbox backlog for /metrics: events recorded but not yet published."""
        pending = await self.events.count_documents({"published": False})
        oldest = await self.events.find_one(
            {"published": False}, {"received_at": 1}, sort=[("received_at", 1)]
        )
        OUTBOX_PENDING.set(pending)
        OUTBOX_OLDEST_AGE.set((utcnow() - oldest["received_at"]).total_seconds() if oldest else 0)

    async def close(self) -> None:
        await self.client.close()
