"""Inventory's Celery worker: ``celery -A inventory.tasks worker -B``.

* ``inventory.release_expired_holds`` — every ``hold_sweep_seconds`` (beat):
  release unconfirmed holds past their expiry, so stock held for a shipment
  that was never recorded goes back on sale without anyone asking for it.
  (A request for that stock also reclaims it on the spot; the sweep covers stock
  nobody is asking for, so availability figures stay right.)
"""

from __future__ import annotations

import logging
from datetime import timedelta

from inventory.config import settings
from inventory.db import database
from inventory.services.reservations import ReservationService
from sl_platform.tasks import create_celery, run_async

logger = logging.getLogger("inventory.tasks")

celery_app = create_celery(
    settings,
    beat_schedule={
        "release-expired-holds": {
            "task": "inventory.release_expired_holds",
            "schedule": float(settings.hold_sweep_seconds),
        },
    },
    engine=database.engine,
)


async def release_expired_holds() -> int:
    async with database.sessionmaker() as session:
        service = ReservationService(
            session, unconfirmed_hold=timedelta(minutes=settings.unconfirmed_hold_minutes)
        )
        released = await service.release_expired()
        await session.commit()
    if released:
        logger.info("released %d expired hold(s)", released)
    return released


@celery_app.task(name="inventory.release_expired_holds")
def release_expired_holds_task() -> int:
    return run_async(release_expired_holds())
