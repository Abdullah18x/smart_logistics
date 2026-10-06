"""Warehouse background process: ``python -m warehouse.worker``. Publishes the outbox."""

import asyncio

from sl_platform.worker import run_worker
from warehouse.config import settings
from warehouse.db import database


def main() -> None:
    asyncio.run(run_worker(settings, database))


if __name__ == "__main__":
    main()
