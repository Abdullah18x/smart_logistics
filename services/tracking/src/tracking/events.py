"""Events Tracking consumes.

* ``courier.assigned`` — a reassigned shipment's updates come from the new courier.
* ``shipment.status_changed`` to ``return_initiated`` / ``returned`` — milestones
  decided outside the field, added to the journey's timeline.

Handlers are naturally idempotent (the recorded event's ``_id`` is the incoming
event id; the reassignment is a plain set), so no dedupe table is needed.
"""

from __future__ import annotations

from datetime import datetime

from sl_platform.consumer import StatelessProcessor
from sl_platform.events import EventEnvelope, Topics
from tracking.milestones import Milestone
from tracking.services.journeys import JourneyService

MIRRORED_STATUSES = {
    "return_initiated": Milestone.RETURN_INITIATED,
    "returned": Milestone.RETURNED,
}


def build_processor(journeys: JourneyService) -> StatelessProcessor:
    async def on_courier_assigned(event: EventEnvelope) -> None:
        await journeys.reassign(str(event.payload["shipment_id"]), str(event.payload["courier_id"]))

    async def on_shipment_status(event: EventEnvelope) -> None:
        milestone = MIRRORED_STATUSES.get(event.payload.get("status", ""))
        if milestone is None:
            return
        await journeys.record_from_shipment(
            event_id=event.event_id,
            shipment_id=str(event.payload["id"]),
            milestone=milestone,
            occurred_at=event.occurred_at
            if isinstance(event.occurred_at, datetime)
            else datetime.fromisoformat(str(event.occurred_at)),
        )

    return StatelessProcessor(
        "tracking",
        handlers={
            "courier.assigned": on_courier_assigned,
            "shipment.status_changed": on_shipment_status,
        },
        topics=(Topics.COURIER, Topics.SHIPMENT),
    )
