"""Events into facts and counters, the nightly rebuild, and dead letters."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select, update

from analytics import rollups
from analytics.db import database
from analytics.dead_letters import record
from analytics.models import Courier, DailyRollup, IssueKind, PipelineIssue, Scope, Shipment
from analytics.services.reports import Reports
from analytics.tasks import recent_period
from tests.conftest import KHI, LHE, at, courier, feed, journey, shipment

NOV = (date(2026, 11, 1), date(2026, 11, 30))


async def counters(scope_id=None, scope=Scope.WAREHOUSE) -> dict[tuple[date, str], int]:
    async with database.sessionmaker() as session:
        query = select(DailyRollup).where(DailyRollup.scope == scope)
        if scope_id:
            query = query.where(DailyRollup.scope_id == scope_id)
        return {(r.day, r.metric): r.value for r in await session.scalars(query)}


async def totals(warehouses=None):
    async with database.sessionmaker() as session:
        return await Reports(session).totals(*NOV, warehouses)


async def test_a_delivered_shipment_moves_each_counter_on_the_day_it_happened():
    courier_id = uuid.uuid4()
    await journey(
        ("created", at(2, 8)),
        ("dispatched", at(2, 10)),
        ("assigned", at(2, 10)),
        ("out_for_delivery", at(3, 9)),
        ("delivered", at(3, 14)),
        courier_id=courier_id,
    )
    assert await counters(KHI) == {
        (date(2026, 11, 2), "created"): 1,
        (date(2026, 11, 2), "dispatched"): 1,
        (date(2026, 11, 3), "delivered"): 1,
        (date(2026, 11, 3), "delivery_seconds"): 30 * 3600,
        (date(2026, 11, 3), "delivered_timed"): 1,
    }
    assert await counters(courier_id, Scope.COURIER) == {
        (date(2026, 11, 2), "assigned"): 1,
        (date(2026, 11, 3), "delivered"): 1,
    }
    assert (await totals()).avg_delivery_hours == 30.0


async def test_a_redelivered_event_counts_once():
    shipment_id = uuid.uuid4()
    event_id = uuid.uuid4()
    for _ in range(2):
        await feed("shipment.created", shipment(shipment_id, "created", 1), at(2), event_id)
    assert (await counters(KHI))[(date(2026, 11, 2), "created")] == 1


async def test_a_late_older_event_counts_but_does_not_roll_the_status_back():
    shipment_id = uuid.uuid4()
    await feed("shipment.created", shipment(shipment_id, "created", 1), at(2))
    await feed("shipment.status_changed", shipment(shipment_id, "delivered", 5), at(4))
    await feed("shipment.status_changed", shipment(shipment_id, "dispatched", 2), at(3))
    async with database.sessionmaker() as session:
        row = await session.get(Shipment, shipment_id)
    assert (row.status, row.version) == ("delivered", 5)
    assert row.dispatched_at == at(3) and row.delivered_at == at(4)
    assert (await totals())[rollups.Metric.DISPATCHED] == 1


async def test_failed_attempts_returns_and_rates():
    first = await journey(("created", at(2)), ("dispatched", at(2, 11)),
                          ("delivery_failed", at(3)), ("delivery_failed", at(4)),
                          ("return_initiated", at(5)))  # fmt: skip
    await journey(("created", at(2)), ("dispatched", at(2, 11)), ("delivered", at(3)))
    async with database.sessionmaker() as session:
        assert (await session.get(Shipment, first)).failed_attempts == 2
    t = await totals()
    assert t.failed_delivery_rate == round(2 / 3, 4)
    assert t.return_rate == 0.5


async def test_a_failed_dispatch_is_counted_and_recorded_as_an_issue():
    shipment_id = await journey(("created", at(2)))
    await feed(
        "shipment.status_changed",
        shipment(shipment_id, "dispatch_failed", 2, reason="no_courier_available: none in KHI"),
        at(2, 12),
    )
    async with database.sessionmaker() as session:
        issue = await session.scalar(select(PipelineIssue))
    assert (issue.kind, issue.source, issue.reason) == (
        IssueKind.DISPATCH_FAILED, "KHI-01", "no_courier_available"
    )  # fmt: skip
    assert issue.shipment_id == shipment_id
    assert (await totals())[rollups.Metric.DISPATCH_FAILED] == 1


async def test_counters_are_kept_per_warehouse():
    await journey(("created", at(2)), ("dispatched", at(2, 11)))
    await journey(("created", at(2)), warehouse=LHE)
    assert (await totals([LHE]))[rollups.Metric.CREATED] == 1
    assert (await totals([LHE]))[rollups.Metric.DISPATCHED] == 0
    assert (await totals())[rollups.Metric.CREATED] == 2


async def test_an_older_courier_snapshot_does_not_overwrite_a_newer_one():
    courier_id = uuid.uuid4()
    await feed("courier.updated", courier(courier_id, active=3, updated_at=at(5)), at(5))
    await feed("courier.updated", courier(courier_id, active=1, updated_at=at(4)), at(4))
    async with database.sessionmaker() as session:
        assert (await session.get(Courier, courier_id)).active_assignments == 3


async def test_utilisation_is_load_over_capacity_of_active_couriers():
    await feed("courier.created", courier(uuid.uuid4(), active=2, capacity=4), at(1))
    await feed("courier.created", courier(uuid.uuid4(), active=0, capacity=4), at(1))
    await feed("courier.created", courier(uuid.uuid4(), active=1, capacity=4, status="suspended"),
               at(1))  # fmt: skip
    async with database.sessionmaker() as session:
        load = await Reports(session).utilisation(None)
    assert (load.active_couriers, load.busy_couriers, load.ratio) == (2, 1, 0.25)


class TestRecompute:
    async def test_it_repairs_counters_from_the_facts(self):
        await journey(("created", at(2, 8)), ("dispatched", at(2, 10)), ("delivered", at(3, 8)))
        before = await counters(KHI)
        async with database.sessionmaker() as session:
            await session.execute(update(DailyRollup).values(value=999))
            session.add(DailyRollup(day=date(2026, 11, 2), scope=Scope.WAREHOUSE, scope_id=KHI,
                                    metric="returned", value=7))  # fmt: skip
            await session.commit()
        async with database.sessionmaker() as session:
            assert await rollups.recompute(session, *NOV) == 3
            await session.commit()
        assert await counters(KHI) == before

    async def test_only_the_chosen_days_are_rebuilt(self):
        await journey(("created", at(2)), ("dispatched", at(5)))
        async with database.sessionmaker() as session:
            await session.execute(update(DailyRollup).values(value=50))
            await session.commit()
            await rollups.recompute(session, date(2026, 11, 5), date(2026, 11, 5))
            await session.commit()
        assert await counters(KHI) == {
            (date(2026, 11, 2), "created"): 50,  # untouched
            (date(2026, 11, 5), "dispatched"): 1,
        }

    def test_the_nightly_run_covers_today_and_yesterday(self):
        assert recent_period(date(2026, 11, 10)) == (date(2026, 11, 9), date(2026, 11, 10))


LETTER = {
    "dlq.consumer": "inventory",
    "dlq.reason": "handler_failed",
    "dlq.error": "RuntimeError: database says no",
    "dlq.event-id": "e-1",
    "dlq.event-type": "shipment.created",
    "dlq.failed-at": "2026-11-04T10:00:00+00:00",
    "dlq.status": "dead",
}


class TestDeadLetters:
    async def test_each_letter_is_recorded_once_and_a_replay_marks_it(self):
        key = str(uuid.uuid4())
        async with database.sessionmaker() as session:
            first = await record(session, LETTER, topic="dlq.inventory", partition=0,
                                 offset=7, key=key)  # fmt: skip
            again = await record(session, LETTER, topic="dlq.inventory", partition=0,
                                 offset=7, key=key)  # fmt: skip
            marker = {**LETTER, "dlq.status": "replayed"}
            replayed = await record(session, marker, topic="dlq.inventory", partition=0, offset=8)
            await session.commit()
            issues = list(await session.scalars(select(PipelineIssue)))
        assert (first, again, replayed) == ("recorded", "duplicate", "replayed")
        (issue,) = issues
        assert (issue.source, issue.reason, issue.status) == ("inventory", "handler_failed",
                                                              "replayed")  # fmt: skip
        assert issue.occurred_at == at(4, 10) and str(issue.shipment_id) == key
        assert "database says no" in issue.detail

    async def test_an_unreadable_letter_still_counts(self):
        async with database.sessionmaker() as session:
            await record(session, {}, topic="dlq.courier", partition=1, offset=0, key="",
                         timestamp_ms=1_762_250_400_000)  # fmt: skip
            await session.commit()
            issue = await session.scalar(select(PipelineIssue))
        assert (issue.source, issue.reason, issue.shipment_id) == ("courier", "unknown", None)
