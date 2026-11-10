"""Notification's Celery worker: ``celery -A notification.tasks worker``.

* ``notification.send`` — one send attempt for one notification. A provider
  error schedules a retry with backoff (5 s, 10 s, 20 s... up to the attempt
  limit), after which the notification is marked failed.
* ``notification.requeue_overdue`` — every minute (beat): queue again anything
  still pending whose attempt is overdue, so a lost enqueue or a broker outage
  only delays a message.
"""

from __future__ import annotations

import logging
import uuid

from notification.channels import default_senders
from notification.config import settings
from notification.db import database
from notification.queue import enqueue
from notification.services.notifications import NotificationService
from sl_platform.tasks import create_celery, run_async

logger = logging.getLogger("notification.tasks")

celery_app = create_celery(
    settings,
    beat_schedule={
        "requeue-overdue": {"task": "notification.requeue_overdue", "schedule": 60.0},
    },
    engine=database.engine,
)
_senders = None


def senders():
    global _senders
    if _senders is None:
        _senders = default_senders(settings)
    return _senders


async def send_one(notification_id: uuid.UUID):
    async with database.sessionmaker() as session:
        result = await NotificationService(session, settings).send(notification_id, senders())
        await session.commit()
    return result


async def requeue_overdue() -> int:
    async with database.sessionmaker() as session:
        ids = await NotificationService(session, settings).overdue()
    await enqueue(ids)
    if ids:
        logger.info("re-queued %d overdue notification(s)", len(ids))
    return len(ids)


@celery_app.task(name="notification.send", bind=True, max_retries=None)
def send_notification(self, notification_id: str) -> str:
    result = run_async(send_one(uuid.UUID(notification_id)))
    if result.retry_in is not None:
        # The attempt count lives in the row, not in Celery: the service decides
        # when to stop. Celery only carries the delay.
        raise self.retry(countdown=result.retry_in)
    return result.status.value


@celery_app.task(name="notification.requeue_overdue")
def requeue_overdue_task() -> int:
    return run_async(requeue_overdue())
