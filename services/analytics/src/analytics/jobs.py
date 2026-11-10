"""Queueing a recompute: the API calls ``jobs.Holder.queue_recompute`` (Celery in
production, a recorder in tests)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date


async def celery_recompute(start: date, end: date) -> None:
    from analytics.tasks import recompute_task

    await asyncio.to_thread(recompute_task.delay, start.isoformat(), end.isoformat())


class Holder:
    queue_recompute: Callable[[date, date], Awaitable[None]] = staticmethod(celery_recompute)
