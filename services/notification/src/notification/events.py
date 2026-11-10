"""Events Notification consumes.

* ``shipment.created`` — keep the recipient's contact details; confirm the order.
* ``shipment.status_changed`` — tell the customer (or operations) about the change.
* ``courier.*`` profile events — keep couriers' contact details.
* ``courier.assigned`` — tell the courier about the new job.

Notification publishes nothing: it is a leaf of the event graph.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from notification.config import settings
from notification.queue import enqueue
from notification.services.notifications import NotificationService
from notification.services.replicas import store_courier, store_recipient
from sl_platform.consumer import EventProcessor, after_commit
from sl_platform.events import EventEnvelope, Topics

PROFILE_EVENTS = (
    "courier.created",
    "courier.updated",
    "courier.status_changed",
    "courier.availability_changed",
    "courier.deleted",
    "courier.restored",
)


async def notify(session: AsyncSession, event: EventEnvelope) -> None:
    ids = await NotificationService(session, settings).create_for_event(event)
    # Queue them once the rows are committed; the sweep catches a lost enqueue.
    after_commit(session, lambda: enqueue(ids))


async def on_shipment_created(session: AsyncSession, event: EventEnvelope) -> None:
    await store_recipient(session, event.payload)
    await notify(session, event)


async def on_courier_profile(session: AsyncSession, event: EventEnvelope) -> None:
    await store_courier(session, event.payload)


def build_processor(sessionmaker: async_sessionmaker[AsyncSession]) -> EventProcessor:
    return EventProcessor(
        "notification",
        sessionmaker,
        handlers={
            "shipment.created": on_shipment_created,
            "shipment.status_changed": notify,
            "courier.assigned": notify,
            **dict.fromkeys(PROFILE_EVENTS, on_courier_profile),
        },
        topics=(Topics.SHIPMENT, Topics.COURIER),
    )
