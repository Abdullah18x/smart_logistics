"""The event envelope every service publishes and consumes.

Events are facts in the past tense (``shipment.created``), keyed by the
aggregate id so every event about one shipment lands on one partition, in
order. Payloads carry the full state a consumer needs (event-carried state
transfer), so consumers never call back to the producer to "fill in" an event.

Schema governance: the envelope is versioned (``version``) and validated with
Pydantic on both sides. Registering these contracts in Confluent Schema
Registry is the Week 3 Kafka milestone; the envelope does not change then.
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

    def to_bytes(self) -> bytes:
        return self.model_dump_json().encode()

    @classmethod
    def from_bytes(cls, raw: bytes) -> EventEnvelope:
        return cls.model_validate_json(raw)
