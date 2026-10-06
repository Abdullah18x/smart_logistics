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

from sl_platform.db import utcnow
from sl_platform.events import EventEnvelope
from sl_platform.models import OutboxEvent
from sl_platform.telemetry import current_traceparent

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
    """Stage an event in the current transaction. Never publishes directly."""
    envelope = EventEnvelope(
        event_type=event_type,
        producer=producer,
        key=str(key),
        payload=json.loads(json.dumps(payload, default=str)),
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
    async def send(self, topic: str, key: str, value: bytes) -> None: ...


class KafkaPublisher:
    """Idempotent Kafka producer: ``acks=all`` and broker-side dedupe of retries."""

    def __init__(self, bootstrap_servers: str) -> None:
        self._bootstrap = bootstrap_servers
        self._producer = None

    async def start(self) -> None:
        from aiokafka import AIOKafkaProducer

        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap, acks="all", enable_idempotence=True
        )
        await self._producer.start()

    async def stop(self) -> None:
        if self._producer is not None:
            await self._producer.stop()

    async def send(self, topic: str, key: str, value: bytes) -> None:
        assert self._producer is not None, "start() the publisher first"
        await self._producer.send_and_wait(topic, value=value, key=key.encode())


class OutboxRelay:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        publisher: Publisher,
        *,
        batch_size: int = 100,
        poll_interval: float = 0.5,
    ) -> None:
        self.sessionmaker = sessionmaker
        self.publisher = publisher
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
                    await self.publisher.send(row.topic, row.key, json.dumps(row.envelope).encode())
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
