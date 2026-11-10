"""Analytics' Celery worker: ``celery -A analytics.tasks worker -B``.

* ``analytics.recompute`` — rebuild the daily rollups of a date range from the
  milestone facts (the incremental counters' safety net).
* Beat runs it nightly (02:17 UTC by default) for the last ``recompute_days``
  days, which covers late events and anything replayed from a dead-letter topic.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from celery.schedules import crontab

from analytics import rollups
from analytics.config import settings
from analytics.db import database
from sl_platform.tasks import create_celery, run_async

logger = logging.getLogger("analytics.tasks")

celery_app = create_celery(
    settings,
    beat_schedule={
        "nightly-recompute": {
            "task": "analytics.recompute_recent",
            "schedule": crontab(
                hour=settings.recompute_hour_utc, minute=settings.recompute_minute_utc
            ),
        },
    },
    engine=database.engine,
)


async def recompute(start: date, end: date) -> int:
    async with database.sessionmaker() as session:
        counted = await rollups.recompute(session, start, end)
        await session.commit()
    logger.info("rebuilt rollups %s..%s from %d milestone(s)", start, end, counted)
    return counted


def recent_period(today: date | None = None) -> tuple[date, date]:
    today = today or datetime.now(UTC).date()
    return today - timedelta(days=max(settings.recompute_days, 1) - 1), today


@celery_app.task(name="analytics.recompute")
def recompute_task(start: str, end: str) -> int:
    return run_async(recompute(date.fromisoformat(start), date.fromisoformat(end)))


@celery_app.task(name="analytics.recompute_recent")
def recompute_recent_task() -> int:
    return run_async(recompute(*recent_period()))
