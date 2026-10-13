"""Publishes recorded tracking events to Kafka (``tracking.events``).

Each ``tracking_events`` document is its own outbox entry. Several relay pods
may run: each *claims* a document with an atomic find-and-update that sets a
short lease, publishes it, then marks it published. A relay that dies after
publishing but before marking leaves the lease to expire and the event is
published again: at-least-once, and consumers dedupe on the event id (which is
the device's ``event_id``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from datetime import UTC, datetime, timedelta

from pymongo import ReturnDocument

from sl_platform.events import EventEnvelope, Topics
from sl_platform.outbox import Publisher
from tracking.store import TrackingStore

logger = logging.getLogger("tracking.relay")

LEASE = timedelta(seconds=30)
EVENT_TYPES = {
    "milestone": "tracking.milestone_recorded",
    "location": "tracking.location_recorded",
}


def envelope_for(event: dict) -> EventEnvelope:
    return EventEnvelope(
        event_id=uuid.UUID(event["_id"]),
        event_type=EVENT_TYPES[event["type"]],
        occurred_at=event["occurred_at"],
        producer="tracking",
        key=event["shipment_id"],
        payload={
            "shipment_id": event["shipment_id"],
            "milestone": event.get("milestone"),
            "occurred_at": event["occurred_at"].isoformat(),
            "courier_id": event.get("courier_id"),
            "location": event.get("location"),
            "failure_reason": event.get("failure_reason"),
            "source": event.get("source"),
        },
    )


class TrackingRelay:
    def __init__(
        self,
        store: TrackingStore,
        publisher: Publisher,
        *,
        batch_size: int = 100,
        poll_interval: float = 0.5,
    ) -> None:
        self.store = store
        self.publisher = publisher
        self.batch_size = batch_size
        self.poll_interval = poll_interval

    async def publish_batch(self) -> int:
        published = 0
        for _ in range(self.batch_size):
            current = datetime.now(UTC)
            event = await self.store.events.find_one_and_update(
                {
                    "published": False,
                    "$or": [
                        {"claimed_until": {"$exists": False}},
                        {"claimed_until": {"$lt": current}},
                    ],
                },
                {"$set": {"claimed_until": current + LEASE}},
                sort=[("received_at", 1)],
                return_document=ReturnDocument.AFTER,
            )
            if event is None:
                break
            envelope = envelope_for(event)
            await self.publisher.send(Topics.TRACKING, envelope.key, envelope.to_bytes())
            await self.store.events.update_one(
                {"_id": event["_id"]},
                {"$set": {"published": True, "published_at": datetime.now(UTC)}},
            )
            published += 1
        return published

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                count = await self.publish_batch()
            except Exception:
                logger.exception("tracking relay iteration failed")
                count = 0
            if count == 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=self.poll_interval)
