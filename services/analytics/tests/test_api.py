"""The metrics over HTTP: who sees what, and the shapes."""

from __future__ import annotations

import uuid
from datetime import date

from analytics.db import database
from analytics.dead_letters import record
from sl_platform.roles import UserRole
from tests.conftest import KHI, LHE, at, auth, courier, feed, journey

NOV = {"from": "2026-11-01", "to": "2026-11-30"}
OPERATOR_KHI = auth(UserRole.WAREHOUSE_OPERATOR, warehouse_ids=(KHI,))


async def busy_month():
    courier_id = uuid.uuid4()
    await feed("courier.created", courier(courier_id, active=2, capacity=8), at(1))
    await journey(("created", at(2, 8)), ("dispatched", at(2, 10)), ("assigned", at(2, 10)),
                  ("delivered", at(2, 20)), courier_id=courier_id)  # fmt: skip
    await journey(("created", at(3)), ("dispatched", at(3, 11)), ("assigned", at(3, 11)),
                  ("delivery_failed", at(4)), ("return_initiated", at(5)),
                  courier_id=courier_id)  # fmt: skip
    await journey(("created", at(3)), ("dispatched", at(4)), warehouse=LHE)
    async with database.sessionmaker() as session:
        await record(session, {"dlq.consumer": "inventory", "dlq.reason": "handler_failed",
                               "dlq.failed-at": at(6).isoformat()},
                     topic="dlq.inventory", partition=0, offset=1)  # fmt: skip
        await session.commit()
    return courier_id


async def test_the_summary_has_all_nine_metrics(client):
    await busy_month()
    body = (await client.get("/api/v1/analytics/summary", params=NOV, headers=auth())).json()
    assert body["period"] == {"from": "2026-11-01", "to": "2026-11-30"}
    assert (body["total_shipments"], body["dispatched"], body["delivered"]) == (3, 3, 1)
    assert (body["failed_deliveries"], body["returns"]) == (1, 1)
    assert body["average_delivery_hours"] == 12.0
    assert body["return_rate"] == round(1 / 3, 4)
    assert body["courier_utilisation"]["utilisation"] == 0.25
    throughput = {w["warehouse_code"]: w for w in body["warehouse_throughput"]}
    assert throughput["KHI-01"]["dispatched"] == 2 and throughput["LHE-01"]["dispatched"] == 1
    assert throughput["KHI-01"]["dispatched_per_day"] == round(2 / 30, 2)
    assert body["failed_events"] == {"dead_letters": 1, "dispatch_failures": 0}


async def test_a_warehouse_operator_sees_only_their_warehouse(client):
    await busy_month()
    body = (await client.get("/api/v1/analytics/summary", params=NOV, headers=OPERATOR_KHI)).json()
    assert body["total_shipments"] == 2 and body["failed_events"] is None
    assert [w["warehouse_code"] for w in body["warehouse_throughput"]] == ["KHI-01"]
    other = await client.get(
        "/api/v1/analytics/summary", params={**NOV, "warehouse_id": str(LHE)}, headers=OPERATOR_KHI
    )
    assert other.status_code == 403
    assert (await client.get("/api/v1/analytics/issues", headers=OPERATOR_KHI)).status_code == 403


async def test_couriers_cannot_read_metrics(client):
    response = await client.get("/api/v1/analytics/summary", headers=auth(UserRole.COURIER))
    assert response.status_code == 403


async def test_a_time_series_is_zero_filled(client):
    await busy_month()
    params = {"metric": "dispatched", "from": "2026-11-01", "to": "2026-11-05"}
    body = (await client.get("/api/v1/analytics/timeseries", params=params, headers=auth())).json()
    assert [p["value"] for p in body["points"]] == [0, 1, 1, 1, 0]
    assert body["total"] == 3 and body["points"][0]["day"] == "2026-11-01"


async def test_bad_periods_and_internal_counters_are_refused(client):
    for params in (
        {"from": "2026-11-05", "to": "2026-11-01"},
        {"from": "2025-01-01", "to": "2026-11-01"},
        {"metric": "delivery_seconds"},
    ):
        response = await client.get("/api/v1/analytics/timeseries", params=params, headers=auth())
        assert response.status_code == 422, params


async def test_per_courier_numbers_and_current_load(client):
    courier_id = await busy_month()
    rows = (await client.get("/api/v1/analytics/couriers", params=NOV, headers=auth())).json()
    (row,) = rows
    assert row["courier_id"] == str(courier_id)
    assert (row["assigned"], row["delivered"], row["failed_deliveries"]) == (2, 1, 1)
    assert row["utilisation"] == 0.25


async def test_issues_list_with_counts(client):
    await busy_month()
    body = (
        await client.get(
            "/api/v1/analytics/issues", params={**NOV, "kind": "dead_letter"}, headers=auth()
        )
    ).json()
    assert body["total"] == 1 and body["items"][0]["source"] == "inventory"
    assert body["counts"] == {"dead_letter": 1, "dispatch_failed": 0}


async def test_only_admins_queue_a_recompute(client, queued):
    body = {"from": "2026-11-01", "to": "2026-11-03"}
    denied = await client.post(
        "/api/v1/analytics/recompute", json=body, headers=auth(UserRole.CUSTOMER_SUPPORT)
    )
    assert denied.status_code == 403
    response = await client.post("/api/v1/analytics/recompute", json=body, headers=auth())
    assert response.status_code == 202
    assert queued == [(date(2026, 11, 1), date(2026, 11, 3))]


async def test_the_summary_is_served_from_the_cache_for_a_while(client, monkeypatch):
    from fakeredis import FakeAsyncRedis

    from analytics.api import deps
    from sl_platform.cache import Cache

    monkeypatch.setattr(deps, "cache", Cache(FakeAsyncRedis(decode_responses=True), "t"))
    await journey(("created", at(2)))
    first = (await client.get("/api/v1/analytics/summary", params=NOV, headers=auth())).json()
    await journey(("created", at(3)))
    cached = (await client.get("/api/v1/analytics/summary", params=NOV, headers=auth())).json()
    assert first == cached and cached["total_shipments"] == 1
    scoped = (
        await client.get("/api/v1/analytics/summary", params=NOV, headers=OPERATOR_KHI)
    ).json()
    assert scoped["total_shipments"] == 2  # a different scope is a different entry
