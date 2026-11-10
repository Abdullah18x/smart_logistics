"""Notification's Kafka worker: ``python -m notification.worker``.

Consumes shipment and courier events and turns them into pending
notifications; the Celery worker (``notification.tasks``) sends them.
"""

import asyncio

from notification.config import settings
from notification.db import database
from notification.events import build_processor
from sl_platform.worker import run_worker


def main() -> None:
    asyncio.run(run_worker(settings, database, [build_processor(database.sessionmaker)]))


if __name__ == "__main__":
    main()
