"""The event envelope every service publishes and consumes.

Events are facts in the past tense (``shipment.created``), keyed by the
aggregate id so every event about one shipment lands on one partition, in
order. Payloads carry the full state a consumer needs (event-carried state
transfer), so consumers never call back to the producer to "fill in" an event.

Schema governance: the envelope is validated with Pydantic on both sides, and
each event type's payload has a JSON Schema contract registered in the Schema
Registry (see ``contracts.py``); ``schema_id`` names the version it was written
with.

Failed events: a consumer that cannot handle an event after its retries (or
receives one that breaks its contract) moves it to its own dead-letter topic,
``dlq.<consumer>``. Replaying sends it to ``retry.<consumer>``, which only that
consumer reads — the services that handled it fine never see it again.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class Topics:
    """One topic per producing service. Consumers filter on ``event_type``."""

    IDENTITY = "identity.events"
    WAREHOUSE = "warehouse.events"
    INVENTORY = "inventory.events"
    SHIPMENT = "shipment.events"
    COURIER = "courier.events"
    TRACKING = "tracking.events"

    @staticmethod
    def dead_letters(consumer: str) -> str:
        return f"dlq.{consumer}"

    @staticmethod
    def retry(consumer: str) -> str:
        return f"retry.{consumer}"


class EventEnvelope(BaseModel):
    event_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    event_type: str = Field(description="e.g. 'shipment.created'")
    version: int = 1
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    producer: str
    #: Partition key — the aggregate id.
    key: str
    payload: dict[str, Any]
    #: W3C trace context, so a trace continues through the broker.
    traceparent: str | None = None
    #: The Schema Registry id of the payload's contract, stamped when published.
    schema_id: int | None = None

    def to_bytes(self) -> bytes:
        return self.model_dump_json().encode()

    @classmethod
    def from_bytes(cls, raw: bytes) -> EventEnvelope:
        return cls.model_validate_json(raw)
