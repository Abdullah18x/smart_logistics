"""Courier background process: ``python -m courier.worker``.

Publishes the outbox (profile and assignment events) and consumes warehouse
snapshots and shipment status changes (which complete or release assignments).
"""

import asyncio

from courier.config import settings
from courier.db import database
from courier.events import build_processor
from sl_platform.worker import run_worker


def main() -> None:
    asyncio.run(run_worker(settings, database, [build_processor(database.sessionmaker)]))


if __name__ == "__main__":
    main()
