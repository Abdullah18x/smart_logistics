"""Transactional outbox: write the event with the change, publish it afterwards.

``add_event`` stages an event in the caller's session, so it commits — or rolls
back — together with the business change. ``OutboxRelay`` runs in the
service's worker process and publishes pending rows to Kafka.

Several relay pods may run at once: each claims rows with
``FOR UPDATE SKIP LOCKED``, so they work on different rows. A relay can still
publish a row and die before marking it sent, so delivery is at-least-once and
consumers dedupe (see ``consumer.py``).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sl_platform import contracts
from sl_platform.db import utcnow
from sl_platform.events import EventEnvelope
from sl_platform.metrics import OUTBOX_PUBLISH_FAILURES, OUTBOX_PUBLISHED
from sl_platform.models import OutboxEvent
from sl_platform.schema_registry import SchemaRegistry, subject_for
from sl_platform.telemetry import current_traceparent, publish_span

logger = logging.getLogger("sl_platform.outbox")


def add_event(
    session: AsyncSession,
    *,
    topic: str,
    event_type: str,
    key: Any,
    payload: dict[str, Any],
    producer: str,
) -> EventEnvelope:
    """Stage an event in the current transaction. Never publishes directly.

    The payload is checked against the event's contract first: a payload that
    breaks it raises ``ContractError`` and the business change rolls back with it.
    """
    body = json.loads(json.dumps(payload, default=str))
    contracts.validate(event_type, body)
    envelope = EventEnvelope(
        event_type=event_type,
        producer=producer,
        key=str(key),
        payload=body,
        traceparent=current_traceparent(),
    )
    session.add(
        OutboxEvent(
            event_id=envelope.event_id,
            topic=topic,
            key=envelope.key,
            event_type=event_type,
            envelope=envelope.model_dump(mode="json"),
        )
    )
    return envelope


class Publisher(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def send(
        self, topic: str, key: str, value: bytes, headers: dict[str, str] | None = None
    ) -> None: ...


def trace_headers(envelope: dict[str, Any]) -> dict[str, str]:
    """W3C trace context as Kafka headers, the OpenTelemetry messaging convention.
    The envelope carries it too (it must survive the outbox); the headers let
    tools that only read headers follow the trace."""
    return {"traceparent": envelope["traceparent"]} if envelope.get("traceparent") else {}


async def stamp_schema(
    registry: SchemaRegistry | None, topic: str, envelope: dict[str, Any]
) -> dict[str, Any]:
    """Register the event type's contract (cached after the first time) and stamp
    its id on the envelope. Without a registry, or without a contract, unchanged."""
    if registry is None:
        return envelope
    schema = contracts.contract_for(envelope["event_type"])
    if schema is None:
        return envelope
    schema_id = await registry.register(subject_for(topic, envelope["event_type"]), schema)
    return {**envelope, "schema_id": schema_id}


async def publish_envelope(
    publisher: Publisher,
    topic: str,
    envelope: dict[str, Any],
    registry: SchemaRegistry | None = None,
) -> None:
    """Publish one stored envelope inside a PRODUCER span, with metrics.

    A registry that is down or refuses the schema raises, so the row stays in
    the outbox and is retried: no event leaves without its contract registered.
    """
    try:
        envelope = await stamp_schema(registry, topic, envelope)
        with publish_span(envelope, topic) as to_send:
            await publisher.send(
                topic, str(to_send["key"]), json.dumps(to_send).encode(), trace_headers(to_send)
            )
    except Exception:
        OUTBOX_PUBLISH_FAILURES.labels(topic).inc()
        raise
    OUTBOX_PUBLISHED.labels(topic).inc()


class KafkaPublisher:
    """Idempotent Kafka producer: ``acks=all`` and broker-side dedupe of retries."""

    def __init__(self, bootstrap_servers: str) -> None:
        self._bootstrap = bootstrap_servers
        self._producer = None

    async def start(self) -> None:
        from aiokafka import AIOKafkaProducer

        producer = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap, acks="all", enable_idempotence=True
        )
        try:
            await producer.start()
        except Exception:
            await producer.stop()  # release the half-open client before retrying
            raise
        self._producer = producer

    async def stop(self) -> None:
        if self._producer is not None:
            await self._producer.stop()

    async def send(
        self, topic: str, key: str, value: bytes, headers: dict[str, str] | None = None
    ) -> None:
        assert self._producer is not None, "start() the publisher first"
        await self._producer.send_and_wait(
            topic,
            value=value,
            key=key.encode(),
            headers=[(k, v.encode()) for k, v in (headers or {}).items()],
        )


class OutboxRelay:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        publisher: Publisher,
        *,
        batch_size: int = 100,
        poll_interval: float = 0.5,
        registry: SchemaRegistry | None = None,
    ) -> None:
        self.sessionmaker = sessionmaker
        self.publisher = publisher
        self.registry = registry
        self.batch_size = batch_size
        self.poll_interval = poll_interval

    async def run_once(self) -> int:
        """Publish one batch. Returns how many events went out."""
        published = 0
        async with self.sessionmaker() as session:
            rows = (
                await session.scalars(
                    select(OutboxEvent)
                    .where(OutboxEvent.published_at.is_(None))
                    .order_by(OutboxEvent.seq)
                    .limit(self.batch_size)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for row in rows:
                try:
                    await publish_envelope(self.publisher, row.topic, row.envelope, self.registry)
                except Exception as exc:
                    row.attempts += 1
                    row.last_error = f"{type(exc).__name__}: {exc}"[:500]
                    logger.warning("outbox publish failed for %s: %s", row.event_id, exc)
                    # Stop here: publishing later rows first would reorder events.
                    break
                row.published_at = utcnow()
                published += 1
            await session.commit()
        return published

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                sent = await self.run_once()
            except Exception:
                logger.exception("outbox relay iteration failed")
                sent = 0
            if sent < self.batch_size:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=self.poll_interval)
