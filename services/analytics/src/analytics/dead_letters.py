"""Watching every ``dlq.*`` topic: each dead letter becomes a pipeline issue.

The dead-letter topics are written by the other services' consumers (see
``sl_platform.dlq``). Analytics reads them with its own consumer group and
records one ``pipeline_issues`` row per letter — keyed by topic, partition and
offset, so reading a letter twice records it once. A replay marker on the topic
flips the matching issue to ``replayed``.

New ``dlq.*`` topics are picked up by pattern without a restart.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analytics.models import IssueKind, PipelineIssue
from sl_platform.worker import retry_until_ready

logger = logging.getLogger("analytics.dead_letters")

H = "dlq."
PATTERN = r"^dlq\..+"
GROUP = "analytics-dead-letters"


def _shipment_id(key: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(key)
    except (TypeError, ValueError):
        return None


async def record(
    session: AsyncSession,
    headers: dict[str, str],
    *,
    topic: str,
    partition: int,
    offset: int,
    key: str = "",
    timestamp_ms: int | None = None,
) -> str:
    """Record one record from a dead-letter topic. Returns what it did:
    'recorded', 'duplicate' or 'replayed'."""
    consumer = headers.get(f"{H}consumer") or topic.removeprefix(H)
    event_id = headers.get(f"{H}event-id") or None
    if headers.get(f"{H}status") == "replayed":
        if event_id:
            await session.execute(
                update(PipelineIssue)
                .where(
                    PipelineIssue.kind == IssueKind.DEAD_LETTER,
                    PipelineIssue.source == consumer,
                    PipelineIssue.event_id == event_id,
                )
                .values(status="replayed")
            )
        return "replayed"
    failed_at = headers.get(f"{H}failed-at")
    if failed_at:
        occurred_at = datetime.fromisoformat(failed_at)
    elif timestamp_ms:
        occurred_at = datetime.fromtimestamp(timestamp_ms / 1000, UTC)
    else:
        occurred_at = datetime.now(UTC)
    inserted = await session.scalar(
        pg_insert(PipelineIssue)
        .values(
            id=uuid.uuid4(),
            kind=IssueKind.DEAD_LETTER,
            source=consumer,
            occurred_at=occurred_at,
            reason=(headers.get(f"{H}reason") or "unknown")[:80],
            detail=headers.get(f"{H}error") or None,
            event_id=event_id,
            event_type=headers.get(f"{H}event-type") or None,
            shipment_id=_shipment_id(key),
            origin=f"{topic}:{partition}:{offset}",
        )
        .on_conflict_do_nothing()
        .returning(PipelineIssue.id)
    )
    return "recorded" if inserted else "duplicate"


class DeadLetterWatcher:
    """A long-running loop for ``run_worker(extra=...)``."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], bootstrap: str) -> None:
        self.sessionmaker = sessionmaker
        self.bootstrap = bootstrap
        self.__name__ = "dead-letter-watcher"

    async def __call__(self, stop: asyncio.Event) -> None:
        from aiokafka import AIOKafkaConsumer

        consumer = AIOKafkaConsumer(
            bootstrap_servers=self.bootstrap,
            group_id=GROUP,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            metadata_max_age_ms=30_000,  # notice new dlq.* topics within 30 s
        )
        if not await retry_until_ready(consumer.start, "dead-letter watcher", stop):
            return
        consumer.subscribe(pattern=PATTERN)
        try:
            while not stop.is_set():
                batch = await consumer.getmany(timeout_ms=1000, max_records=200)
                if not batch:
                    continue
                async with self.sessionmaker() as session:
                    for messages in batch.values():
                        for m in messages:
                            await record(
                                session,
                                {k: v.decode() for k, v in (m.headers or ())},
                                topic=m.topic,
                                partition=m.partition,
                                offset=m.offset,
                                key=(m.key or b"").decode(errors="replace"),
                                timestamp_ms=m.timestamp,
                            )
                    await session.commit()
                await consumer.commit()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("dead-letter watcher stopped; the worker restart resumes it")
            raise
        finally:
            await consumer.stop()
