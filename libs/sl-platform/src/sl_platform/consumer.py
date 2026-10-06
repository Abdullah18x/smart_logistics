"""Exactly-once *effects* on top of at-least-once delivery.

Kafka may deliver an event twice. ``EventProcessor`` records the event id in
``processed_events`` in the same transaction as the handler's writes; the
primary key makes the second delivery a no-op, whichever pod receives it.

``KafkaConsumerRunner`` commits the Kafka offset only after the database
commit, so a crash in between causes a redelivery — which the dedupe absorbs —
never a lost event.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sl_platform.events import EventEnvelope
from sl_platform.models import ProcessedEvent

logger = logging.getLogger("sl_platform.consumer")

Handler = Callable[[AsyncSession, EventEnvelope], Awaitable[None]]


class EventProcessor:
    def __init__(
        self,
        name: str,
        sessionmaker: async_sessionmaker[AsyncSession],
        handlers: dict[str, Handler],
        topics: tuple[str, ...],
    ) -> None:
        self.name = name
        self.sessionmaker = sessionmaker
        self.handlers = handlers
        self.topics = topics

    async def process(self, envelope: EventEnvelope) -> bool:
        """Handle one event. Returns False when it was ignored or a duplicate."""
        handler = self.handlers.get(envelope.event_type)
        if handler is None:
            return False
        async with self.sessionmaker() as session:
            claimed = await session.scalar(
                pg_insert(ProcessedEvent)
                .values(
                    consumer=self.name,
                    event_id=envelope.event_id,
                    event_type=envelope.event_type,
                )
                .on_conflict_do_nothing()
                .returning(ProcessedEvent.event_id)
            )
            if claimed is None:
                logger.info("%s skipped duplicate %s", self.name, envelope.event_id)
                return False
            try:
                await handler(session, envelope)
                await session.commit()
            except Exception:
                await session.rollback()
                raise
        return True


class KafkaConsumerRunner:
    def __init__(
        self,
        processor: EventProcessor,
        *,
        bootstrap_servers: str,
        max_attempts: int = 5,
    ) -> None:
        self.processor = processor
        self.bootstrap_servers = bootstrap_servers
        self.max_attempts = max_attempts

    async def run(self, stop: asyncio.Event) -> None:
        from aiokafka import AIOKafkaConsumer

        consumer = AIOKafkaConsumer(
            *self.processor.topics,
            bootstrap_servers=self.bootstrap_servers,
            group_id=self.processor.name,
            enable_auto_commit=False,
            # A new consumer group replays the topic from the start, which is
            # how a fresh service builds its local read models.
            auto_offset_reset="earliest",
        )
        await consumer.start()
        try:
            while not stop.is_set():
                batch = await consumer.getmany(timeout_ms=500)
                for _, messages in batch.items():
                    for message in messages:
                        await self._handle(message.value)
                if batch:
                    await consumer.commit()
        finally:
            await consumer.stop()

    async def _handle(self, raw: bytes) -> None:
        try:
            envelope = EventEnvelope.from_bytes(raw)
        except Exception:
            logger.exception("%s dropped an unparseable message", self.processor.name)
            return
        for attempt in range(1, self.max_attempts + 1):
            try:
                await self.processor.process(envelope)
                return
            except Exception:
                logger.exception(
                    "%s failed on %s (attempt %d/%d)",
                    self.processor.name,
                    envelope.event_id,
                    attempt,
                    self.max_attempts,
                )
                await asyncio.sleep(min(2**attempt, 30))
        # Poison message: logged with its id for replay. A dead-letter topic
        # replaces this in the Week 3 Kafka milestone.
        logger.error("%s gave up on %s", self.processor.name, envelope.event_id)


class LocalEventBus:
    """In-process stand-in for Kafka, for tests and single-process demos.

    Implements the ``Publisher`` protocol and delivers each message to every
    processor subscribed to its topic.
    """

    def __init__(self) -> None:
        self.processors: list[EventProcessor] = []
        self.sent: list[tuple[str, str, EventEnvelope]] = []

    def subscribe(self, processor: EventProcessor) -> None:
        self.processors.append(processor)

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def send(self, topic: str, key: str, value: bytes) -> None:
        envelope = EventEnvelope.from_bytes(value)
        self.sent.append((topic, key, envelope))
        for processor in self.processors:
            if topic in processor.topics:
                await processor.process(envelope)
