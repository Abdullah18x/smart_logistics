"""Entry point for a service's background process: outbox relay + consumers.

The HTTP process and the worker process run from the same image with
different commands, so they scale independently (ADR-013).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from collections.abc import Awaitable, Callable, Sequence

from sqlalchemy import text

from sl_platform.config import ServiceSettings
from sl_platform.consumer import EventProcessor, KafkaConsumerRunner
from sl_platform.db import Database
from sl_platform.logging_setup import configure_logging
from sl_platform.outbox import KafkaPublisher, OutboxRelay

logger = logging.getLogger("sl_platform.worker")


async def retry_until_ready(
    connect: Callable[[], Awaitable[None]],
    what: str,
    stop: asyncio.Event,
    *,
    max_delay: float = 30.0,
) -> bool:
    """Call ``connect`` until it succeeds, backing off between attempts.

    Kafka being down — not started yet, restarting, a network blip — must not
    kill a worker: it waits and resumes, and the outbox holds every event
    until then. Returns False if asked to stop before a connection was made.
    """
    delay, attempt = 1.0, 1
    while not stop.is_set():
        try:
            await connect()
        except Exception as exc:
            logger.warning(
                "%s: Kafka unavailable (%s), retrying in %.0fs (attempt %d)",
                what,
                exc.__class__.__name__,
                delay,
                attempt,
            )
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=delay)
            delay, attempt = min(delay * 2, max_delay), attempt + 1
        else:
            if attempt > 1:
                logger.info("%s: connected to Kafka", what)
            return True
    return False


#: Platform tables every SQL service creates in its first migration.
REQUIRED_TABLES = ("outbox_events", "processed_events")


async def missing_tables(database: Database) -> list[str]:
    """The platform tables not present in the service's database."""
    async with database.engine.connect() as connection:
        found = {
            name: (await connection.execute(text("SELECT to_regclass(:t)"), {"t": name})).scalar()
            for name in REQUIRED_TABLES
        }
    return [name for name, oid in found.items() if oid is None]


async def wait_for_schema(
    database: Database, service: str, stop: asyncio.Event, *, max_delay: float = 30.0
) -> bool:
    """Wait until the database is reachable and migrated.

    A worker started before ``migrate`` must not relay or consume: consumers
    would take events they cannot record. It says once, plainly, what to run,
    and checks again with backoff. Returns False if asked to stop first.
    """
    delay, told = 2.0, None
    while not stop.is_set():
        try:
            missing = await missing_tables(database)
        except Exception as exc:
            message = f"database unreachable ({exc.__class__.__name__}); is it running?"
        else:
            if not missing:
                if told:
                    logger.info("database schema found; starting")
                return True
            message = (
                f"database not migrated (missing {', '.join(missing)}). "
                f"Run the migrations:  ./dev.sh migrate {service}"
            )
        if message != told:
            logger.error("%s — waiting", message)
            told = message
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=delay)
        delay = min(delay * 2, max_delay)
    return False


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

    if not await wait_for_schema(database, settings.service_name, stop):
        await database.dispose()
        return
    publisher = KafkaPublisher(settings.kafka_bootstrap_servers)
    if not await retry_until_ready(publisher.start, "outbox relay", stop):
        await database.dispose()
        return
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
