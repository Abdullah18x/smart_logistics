"""The shared outbox relay and consumer dedupe, exercised against a real database."""

import uuid

from sqlalchemy import func, select

from identity.db import database
from identity.events import build_processor
from sl_platform.consumer import LocalEventBus
from sl_platform.events import EventEnvelope
from sl_platform.models import OutboxEvent, ProcessedEvent
from sl_platform.outbox import OutboxRelay, add_event


class FlakyPublisher:
    def __init__(self, fail_on: int) -> None:
        self.fail_on = fail_on
        self.sent: list[str] = []

    async def send(self, topic, key, value):
        if len(self.sent) == self.fail_on:
            self.fail_on = -1
            raise ConnectionError("broker down")
        self.sent.append(key)


async def stage(keys):
    async with database.sessionmaker() as session:
        for key in keys:
            add_event(
                session, topic="t", event_type="x.happened", key=key, payload={}, producer="test"
            )
        await session.commit()


class TestOutboxRelay:
    async def test_publishes_in_commit_order_and_marks_rows_sent(self):
        await stage(["a", "b", "c"])
        publisher = FlakyPublisher(fail_on=-1)
        relay = OutboxRelay(database.sessionmaker, publisher)
        assert await relay.run_once() == 3
        assert publisher.sent == ["a", "b", "c"]
        assert await relay.run_once() == 0

    async def test_a_failure_stops_the_batch_so_order_is_preserved(self):
        await stage(["a", "b", "c"])
        publisher = FlakyPublisher(fail_on=1)
        relay = OutboxRelay(database.sessionmaker, publisher)
        assert await relay.run_once() == 1
        assert await relay.run_once() == 2
        assert publisher.sent == ["a", "b", "c"]
        async with database.sessionmaker() as session:
            row = await session.scalar(select(OutboxEvent).where(OutboxEvent.key == "b"))
            assert row.attempts == 1 and "broker down" in row.last_error


class TestConsumerDedupe:
    async def test_a_redelivered_event_is_processed_once(self):
        processor = build_processor(database.sessionmaker)
        event = EventEnvelope(
            event_type="warehouse.deleted",
            producer="warehouse",
            key="w",
            payload={"id": str(uuid.uuid4())},
        )
        assert await processor.process(event) is True
        assert await processor.process(event) is False
        async with database.sessionmaker() as session:
            assert await session.scalar(select(func.count()).select_from(ProcessedEvent)) == 1

    async def test_events_without_a_handler_are_ignored(self):
        processor = build_processor(database.sessionmaker)
        event = EventEnvelope(event_type="warehouse.created", producer="w", key="w", payload={})
        assert await processor.process(event) is False

    async def test_local_bus_routes_by_topic(self):
        bus = LocalEventBus()
        bus.subscribe(build_processor(database.sessionmaker))
        event = EventEnvelope(
            event_type="warehouse.deleted",
            producer="warehouse",
            key="w",
            payload={"id": str(uuid.uuid4())},
        )
        await bus.send("warehouse.events", "w", event.to_bytes())
        await bus.send("other.events", "w", event.to_bytes())
        async with database.sessionmaker() as session:
            assert await session.scalar(select(func.count()).select_from(ProcessedEvent)) == 1
