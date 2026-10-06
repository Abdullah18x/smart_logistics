"""Inventory background process: ``python -m inventory.worker``.

Publishes the outbox and consumes warehouse snapshots and shipment lifecycle
events (confirm on created, commit on dispatched, release on cancelled).
"""

import asyncio

from inventory.config import settings
from inventory.db import database
from inventory.events import build_processor
from sl_platform.worker import run_worker


def main() -> None:
    asyncio.run(run_worker(settings, database, [build_processor(database.sessionmaker)]))


if __name__ == "__main__":
    main()
