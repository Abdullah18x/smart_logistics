"""Handing notifications to the Celery workers.

A tiny seam: the consumer and the API call ``queue.enqueue(ids)``; production
publishes one ``notification.send`` task per id to RabbitMQ, tests swap in a
recording queue and drive the send step themselves.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterable
from typing import Protocol


class NotificationQueue(Protocol):
    async def enqueue(self, ids: Iterable[uuid.UUID]) -> None: ...


class CeleryQueue:
    async def enqueue(self, ids: Iterable[uuid.UUID]) -> None:
        from notification.tasks import send_notification

        for nid in ids:
            # Publishing to the broker is blocking I/O: keep it off the event loop.
            await asyncio.to_thread(send_notification.delay, str(nid))


class Holder:
    """Where the active queue lives (swapped in tests)."""

    queue: NotificationQueue = CeleryQueue()


async def enqueue(ids: Iterable[uuid.UUID]) -> None:
    ids = list(ids)
    if ids:
        await Holder.queue.enqueue(ids)
