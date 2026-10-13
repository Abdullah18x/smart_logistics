"""Shipment mirrors ``courier_id`` from the courier service's assignment events."""

import uuid

from shipment.db import database
from shipment.events import build_processor
from shipment.models import Shipment
from sl_platform.events import EventEnvelope
from tests.test_shipments import create


def assignment_event(event_type: str, shipment_id, courier_id) -> EventEnvelope:
    return EventEnvelope(
        event_type=event_type,
        producer="courier",
        key=str(shipment_id),
        payload={"shipment_id": str(shipment_id), "courier_id": str(courier_id)},
    )


async def courier_of(shipment_id) -> uuid.UUID | None:
    async with database.sessionmaker() as session:
        return (await session.get(Shipment, uuid.UUID(shipment_id))).courier_id


async def test_assignment_events_set_and_clear_the_courier(client, inventory):
    shipment = await create(client)
    processor = build_processor(database.sessionmaker)
    first, second = uuid.uuid4(), uuid.uuid4()

    await processor.process(assignment_event("courier.assigned", shipment["id"], first))
    assert await courier_of(shipment["id"]) == first

    # A reassignment arrives as unassigned(first) then assigned(second).
    await processor.process(assignment_event("courier.unassigned", shipment["id"], first))
    await processor.process(assignment_event("courier.assigned", shipment["id"], second))
    assert await courier_of(shipment["id"]) == second

    # A stale "unassigned" for the previous courier changes nothing.
    await processor.process(assignment_event("courier.unassigned", shipment["id"], first))
    assert await courier_of(shipment["id"]) == second


async def test_events_for_unknown_shipments_are_ignored(client):
    processor = build_processor(database.sessionmaker)
    assert await processor.process(assignment_event("courier.assigned", uuid.uuid4(), uuid.uuid4()))
