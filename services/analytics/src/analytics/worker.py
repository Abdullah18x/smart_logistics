"""Analytics' Kafka worker: ``python -m analytics.worker``.

Consumes shipment and courier events into facts and daily counters, and watches
every ``dlq.*`` topic for failed events.
"""

import asyncio

from analytics.config import settings
from analytics.db import database
from analytics.dead_letters import DeadLetterWatcher
from analytics.events import build_processor
from sl_platform.worker import run_worker


def main() -> None:
    asyncio.run(
        run_worker(
            settings,
            database,
            [build_processor(database.sessionmaker)],
            extra=[DeadLetterWatcher(database.sessionmaker, settings.kafka_bootstrap_servers)],
        )
    )


if __name__ == "__main__":
    main()
