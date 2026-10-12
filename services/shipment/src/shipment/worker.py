"""Shipment background process: ``python -m shipment.worker``.

Publishes the outbox (``shipment.*`` events, which Inventory reacts to) and
consumes warehouse snapshots into the local ``warehouse_refs`` replica.
"""

import asyncio

from shipment.config import settings
from shipment.db import database
from shipment.events import build_processor
from sl_platform.worker import run_worker


def main() -> None:
    asyncio.run(run_worker(settings, database, [build_processor(database.sessionmaker)]))


if __name__ == "__main__":
    main()
