"""Entry point for a service's background process: outbox relay + consumers.

The HTTP process and the worker process run from the same image with
different commands, so they scale independently (ADR-001).
"""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Sequence

from sl_platform.config import ServiceSettings
from sl_platform.consumer import EventProcessor, KafkaConsumerRunner
from sl_platform.db import Database
from sl_platform.logging_setup import configure_logging
from sl_platform.outbox import KafkaPublisher, OutboxRelay

logger = logging.getLogger("sl_platform.worker")


async def run_worker(
    settings: ServiceSettings,
    database: Database,
    processors: Sequence[EventProcessor] = (),
) -> None:
    configure_logging(f"{settings.service_name}-worker", settings.log_level, settings.log_json)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    publisher = KafkaPublisher(settings.kafka_bootstrap_servers)
    await publisher.start()
    relay = OutboxRelay(database.sessionmaker, publisher)
    tasks = [asyncio.create_task(relay.run_forever(stop), name="outbox-relay")]
    for processor in processors:
        runner = KafkaConsumerRunner(processor, bootstrap_servers=settings.kafka_bootstrap_servers)
        tasks.append(asyncio.create_task(runner.run(stop), name=f"consumer-{processor.name}"))
    logger.info("worker started: relay + %d consumer(s)", len(processors))
    try:
        await stop.wait()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await publisher.stop()
        await database.dispose()
