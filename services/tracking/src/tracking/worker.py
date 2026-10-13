"""Tracking background process: ``python -m tracking.worker``.

Publishes recorded updates to ``tracking.events`` (the Mongo-backed outbox
relay) and consumes courier reassignments and shipment status changes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from sl_platform.consumer import KafkaConsumerRunner
from sl_platform.logging_setup import configure_logging
from sl_platform.outbox import KafkaPublisher
from sl_platform.worker import retry_until_ready
from tracking.config import settings
from tracking.events import build_processor
from tracking.relay import TrackingRelay
from tracking.services.journeys import JourneyService
from tracking.store import TrackingStore

logger = logging.getLogger("tracking.worker")


async def run() -> None:
    configure_logging("tracking-worker", settings.log_level, settings.log_json)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    store = TrackingStore(settings)
    publisher = KafkaPublisher(settings.kafka_bootstrap_servers)
    if not await retry_until_ready(publisher.start, "tracking relay", stop):
        await store.close()
        return
    journeys = JourneyService(store, max_clock_skew_seconds=settings.max_clock_skew_seconds)
    relay = TrackingRelay(store, publisher)
    consumer = KafkaConsumerRunner(
        build_processor(journeys), bootstrap_servers=settings.kafka_bootstrap_servers
    )
    tasks = [
        asyncio.create_task(relay.run_forever(stop), name="tracking-relay"),
        asyncio.create_task(consumer.run(stop), name="tracking-consumer"),
    ]
    logger.info("tracking worker started")
    try:
        await stop.wait()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await publisher.stop()
        await store.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
