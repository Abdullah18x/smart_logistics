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
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sl_platform import contracts
from sl_platform.events import EventEnvelope, Topics
from sl_platform.metrics import (
    CONSUMER_LAG,
    EVENT_END_TO_END_SECONDS,
    EVENT_HANDLE_SECONDS,
    EVENTS_CONSUMED,
    EVENTS_UNVALIDATED,
)
from sl_platform.models import ProcessedEvent
from sl_platform.schema_registry import RegistryError, SchemaRegistry
from sl_platform.telemetry import consume_span

if TYPE_CHECKING:
    from sl_platform.dlq import DeadLetters

logger = logging.getLogger("sl_platform.consumer")

Handler = Callable[[AsyncSession, EventEnvelope], Awaitable[None]]
AfterCommit = Callable[[], Awaitable[None]]


def after_commit(session: AsyncSession, callback: AfterCommit) -> None:
    """Run ``callback`` once the handler's transaction has committed — e.g. to
    queue background work for rows the handler just wrote. It is best effort:
    if it fails (or the process dies first), the work must be recoverable from
    the committed rows, typically by a periodic sweep."""
    session.info.setdefault("after_commit", []).append(callback)


async def _run_after_commit(session: AsyncSession, consumer: str) -> None:
    for callback in session.info.pop("after_commit", []):
        try:
            await callback()
        except Exception:
            logger.exception("%s: after-commit step failed; the sweep will recover it", consumer)


Outcome = Literal["processed", "duplicate", "ignored"]


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
        return await self.handle(envelope) == "processed"

    async def handle(self, envelope: EventEnvelope) -> Outcome:
        handler = self.handlers.get(envelope.event_type)
        if handler is None:
            return "ignored"
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
                return "duplicate"
            try:
                await handler(session, envelope)
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            await _run_after_commit(session, self.name)
        return "processed"


EnvelopeHandler = Callable[[EventEnvelope], Awaitable[None]]


class StatelessProcessor:
    """An event processor for services without a SQL database.

    No ``processed_events`` table: each handler must be naturally idempotent
    (an insert keyed by the event id, a conditional update, a Temporal
    workflow id). Runs under ``KafkaConsumerRunner`` like ``EventProcessor``.
    """

    def __init__(
        self, name: str, handlers: dict[str, EnvelopeHandler], topics: tuple[str, ...]
    ) -> None:
        self.name = name
        self.handlers = handlers
        self.topics = topics

    async def process(self, envelope: EventEnvelope) -> bool:
        return await self.handle(envelope) == "processed"

    async def handle(self, envelope: EventEnvelope) -> Outcome:
        handler = self.handlers.get(envelope.event_type)
        if handler is None:
            return "ignored"
        await handler(envelope)
        return "processed"


class KafkaConsumerRunner:
    """Runs one processor against Kafka: its topics plus its own retry topic.

    The offset is committed only after a batch is fully handled — processed,
    or moved to the dead-letter topic — so a crash replays, never loses.
    """

    def __init__(
        self,
        processor: EventProcessor | StatelessProcessor,
        *,
        bootstrap_servers: str,
        max_attempts: int = 5,
        registry: SchemaRegistry | None = None,
        dead_letters: DeadLetters | None = None,
    ) -> None:
        self.processor = processor
        self.bootstrap_servers = bootstrap_servers
        self.max_attempts = max_attempts
        self.registry = registry
        self.dead_letters = dead_letters

    @property
    def topics(self) -> tuple[str, ...]:
        return (*self.processor.topics, Topics.retry(self.processor.name))

    async def run(self, stop: asyncio.Event) -> None:
        from aiokafka import AIOKafkaConsumer

        from sl_platform.worker import retry_until_ready

        consumer = None

        async def connect() -> None:
            nonlocal consumer
            await ensure_topics(self.bootstrap_servers, [Topics.retry(self.processor.name)])
            candidate = AIOKafkaConsumer(
                *self.topics,
                bootstrap_servers=self.bootstrap_servers,
                group_id=self.processor.name,
                enable_auto_commit=False,
                # A new consumer group replays the topic from the start, which is
                # how a fresh service builds its local read models.
                auto_offset_reset="earliest",
                metadata_max_age_ms=30_000,
            )
            try:
                await candidate.start()
            except Exception:
                await candidate.stop()
                raise
            consumer = candidate

        if not await retry_until_ready(connect, f"consumer {self.processor.name}", stop):
            return
        try:
            while not stop.is_set():
                batch = await consumer.getmany(timeout_ms=500)
                for _, messages in batch.items():
                    for message in messages:
                        await receive(
                            self.processor,
                            message.value,
                            topic=message.topic,
                            key=(message.key or b"").decode() or None,
                            registry=self.registry,
                            dead_letters=self.dead_letters,
                            max_attempts=self.max_attempts,
                        )
                if batch:
                    await consumer.commit()
                await record_lag(consumer, self.processor.name)
        finally:
            await consumer.stop()


async def ensure_topics(bootstrap_servers: str, topics: list[str]) -> None:
    """Create topics a consumer subscribes to before anyone has written to them
    (its retry topic), so it is assigned their partitions straight away instead
    of logging "not found" and noticing them on a later metadata refresh. Best
    effort: the broker also creates topics on first write."""
    try:
        from aiokafka.admin import AIOKafkaAdminClient, NewTopic
        from aiokafka.errors import TopicAlreadyExistsError
    except ImportError:
        return
    admin = AIOKafkaAdminClient(bootstrap_servers=bootstrap_servers)
    try:
        await admin.start()
        existing = set(await admin.list_topics())
        missing = [t for t in topics if t not in existing]
        if missing:
            await admin.create_topics(
                [NewTopic(name=t, num_partitions=1, replication_factor=1) for t in missing]
            )
    except TopicAlreadyExistsError:
        pass
    except Exception as exc:
        logger.debug("could not pre-create %s: %s", topics, exc)
    finally:
        await admin.close()


async def receive(
    processor: EventProcessor | StatelessProcessor,
    raw: bytes,
    *,
    topic: str | None = None,
    key: str | None = None,
    registry: SchemaRegistry | None = None,
    dead_letters: DeadLetters | None = None,
    max_attempts: int = 5,
    backoff: Callable[[int], float] = lambda attempt: min(2**attempt, 30),
) -> str:
    """Everything that happens to one Kafka message: parse it, check its
    contract, handle it with retries, and dead-letter it when it cannot be
    handled. Returns the outcome."""
    name = processor.name
    envelope: EventEnvelope | None = None

    async def dead_letter(reason: str, error: str, attempts: int = 0) -> str:
        what = envelope.event_id if envelope else "an unreadable message"
        if dead_letters is None:
            logger.error("%s gave up on %s (%s): %s", name, what, reason, error[:300])
            return "gave_up"
        await dead_letters.send(
            name,
            raw,
            reason=reason,
            error=error,
            source_topic=topic,
            key=key,
            event_id=str(envelope.event_id) if envelope else None,
            event_type=envelope.event_type if envelope else None,
            attempts=attempts,
        )
        logger.error(
            "%s moved %s to %s (%s): %s",
            name,
            what,
            Topics.dead_letters(name),
            reason,
            error[:300],
        )
        return "dead_lettered"

    try:
        envelope = EventEnvelope.from_bytes(raw)
    except Exception as exc:
        outcome = await dead_letter("unparseable", f"{type(exc).__name__}: {exc}")
        EVENTS_CONSUMED.labels(name, "unknown", outcome).inc()
        return outcome

    if envelope.event_type in processor.handlers:
        problem = await check_contract(name, envelope, registry)
        if problem is not None:
            outcome = await dead_letter("contract_violation", problem)
            EVENTS_CONSUMED.labels(name, envelope.event_type, outcome).inc()
            return outcome

    async def give_up(error: BaseException) -> str:
        return await dead_letter("handler_failed", f"{type(error).__name__}: {error}", max_attempts)

    return await handle_with_retries(
        processor, envelope, max_attempts=max_attempts, backoff=backoff, on_give_up=give_up
    )


async def check_contract(
    consumer: str, envelope: EventEnvelope, registry: SchemaRegistry | None
) -> str | None:
    """None if the payload matches the schema it was published with (or cannot
    be checked); otherwise what is wrong with it.

    A consumer never refuses an event because the *registry* is down: the
    producer already validated it, so availability wins and the miss is counted.
    """
    if registry is None:
        return None
    if envelope.schema_id is None:
        EVENTS_UNVALIDATED.labels(consumer, "no_schema_id").inc()
        return None
    try:
        schema = await registry.schema(envelope.schema_id)
    except RegistryError:
        EVENTS_UNVALIDATED.labels(consumer, "registry_unavailable").inc()
        logger.warning("%s: registry unavailable, %s not validated", consumer, envelope.event_id)
        return None
    try:
        contracts.validate_with(schema, envelope.payload)
    except contracts.ContractError as exc:
        return f"schema {envelope.schema_id}: {exc}"
    return None


async def record_lag(consumer, name: str) -> None:
    """Messages not yet reached, per assigned partition (high-water mark minus
    this group's position). Rising lag means the consumer cannot keep up."""
    for partition in consumer.assignment():
        highwater = consumer.highwater(partition)
        if highwater is None:
            continue
        position = await consumer.position(partition)
        CONSUMER_LAG.labels(name, partition.topic, str(partition.partition)).set(
            max(highwater - position, 0)
        )


async def handle_with_retries(
    processor: EventProcessor | StatelessProcessor,
    envelope: EventEnvelope,
    *,
    max_attempts: int = 5,
    backoff: Callable[[int], float] = lambda attempt: min(2**attempt, 30),
    on_give_up: Callable[[BaseException], Awaitable[str]] | None = None,
) -> str:
    """Handle one event inside a CONSUMER span, retrying a failing handler, and
    record the outcome. Returns the outcome; when every attempt fails,
    ``on_give_up`` (the dead-letter step) decides it."""
    name, event_type = processor.name, envelope.event_type
    last_error: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        started = time.perf_counter()
        try:
            with consume_span(
                event_type=event_type,
                event_id=envelope.event_id,
                traceparent=envelope.traceparent,
                consumer=name,
            ):
                outcome = await processor.handle(envelope)
        except Exception as exc:
            last_error = exc
            EVENTS_CONSUMED.labels(name, event_type, "failed").inc()
            logger.exception(
                "%s failed on %s (attempt %d/%d)", name, envelope.event_id, attempt, max_attempts
            )
            if attempt < max_attempts:
                await asyncio.sleep(backoff(attempt))
            continue
        EVENTS_CONSUMED.labels(name, event_type, outcome).inc()
        if outcome == "processed":
            EVENT_HANDLE_SECONDS.labels(name, event_type).observe(time.perf_counter() - started)
            lag = (datetime.now(UTC) - envelope.occurred_at).total_seconds()
            EVENT_END_TO_END_SECONDS.labels(name, event_type).observe(max(lag, 0.0))
        return outcome
    # A poison event: it goes to the dead-letter topic (when there is one) so the
    # partition behind it keeps moving.
    outcome = "gave_up"
    if on_give_up is not None and last_error is not None:
        outcome = await on_give_up(last_error)
    else:
        logger.error("%s gave up on %s", name, envelope.event_id)
    EVENTS_CONSUMED.labels(name, event_type, outcome).inc()
    return outcome


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

    async def send(
        self, topic: str, key: str, value: bytes, headers: dict[str, str] | None = None
    ) -> None:
        envelope = EventEnvelope.from_bytes(value)
        self.sent.append((topic, key, envelope))
        for processor in self.processors:
            if topic in processor.topics:
                await processor.process(envelope)
