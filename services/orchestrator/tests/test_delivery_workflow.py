"""DeliveryWorkflow: field reports become legal shipment transitions."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from temporalio import activity
from temporalio.worker import Worker

from orchestrator.contracts import (
    ApplyStatusInput,
    DeliveryInput,
    FieldUpdate,
    ReassignInput,
)
from orchestrator.workflows.delivery import DeliveryWorkflow, steps_towards


class TestStepsTowards:
    @pytest.mark.parametrize(
        ("current", "reported", "expected"),
        [
            ("dispatched", "picked_up", ["picked_up"]),
            ("dispatched", "out_for_delivery", ["picked_up", "in_transit", "out_for_delivery"]),
            ("in_transit", "picked_up", []),  # stale
            ("in_transit", "in_transit", []),  # repeated
            ("in_transit", "delivered", ["out_for_delivery", "delivered"]),
            ("out_for_delivery", "delivery_failed", ["delivery_failed"]),
            ("delivery_failed", "out_for_delivery", ["out_for_delivery"]),
            ("delivery_failed", "delivered", ["out_for_delivery", "delivered"]),
            ("delivery_failed", "delivery_failed", ["out_for_delivery", "delivery_failed"]),
            ("dispatched", "returned", []),
        ],
    )
    def test_paths(self, current, reported, expected):
        assert steps_towards(current, reported) == expected


class Shipment:
    def __init__(self) -> None:
        self.applied: list[str] = []
        self.reasons: dict[str, str | None] = {}
        self.reassigned = 0

    def activities(self) -> list:
        shipment = self

        @activity.defn(name="apply_delivery_status")
        async def apply_delivery_status(request: ApplyStatusInput) -> None:
            shipment.applied.append(request.status)
            shipment.reasons[request.status] = request.reason

        @activity.defn(name="reassign_overdue_pickup")
        async def reassign_overdue_pickup(request: ReassignInput) -> bool:
            shipment.reassigned += 1
            return True

        return [apply_delivery_status, reassign_overdue_pickup]


def report(milestone: str, reason: str | None = None, event_id: str | None = None) -> FieldUpdate:
    return FieldUpdate(
        event_id=event_id or str(uuid.uuid4()),
        milestone=milestone,
        occurred_at="2026-10-12T10:00:00+00:00",
        failure_reason=reason,
    )


async def deliver(env, updates: list[FieldUpdate], **overrides):
    shipment = Shipment()
    queue = f"test-{uuid.uuid4()}"
    request = DeliveryInput(
        shipment_id=str(uuid.uuid4()),
        courier_id=str(uuid.uuid4()),
        pickup_timeout_seconds=overrides.pop("pickup_timeout_seconds", 3600),
        max_attempts=overrides.pop("max_attempts", 3),
    )
    async with Worker(
        env.client, task_queue=queue, workflows=[DeliveryWorkflow], activities=shipment.activities()
    ):
        handle = await env.client.start_workflow(
            DeliveryWorkflow.run, request, id=f"delivery-{request.shipment_id}", task_queue=queue
        )
        if overrides.pop("wait_before", 0):
            await asyncio.sleep(1.5)
        for update in updates:
            await handle.signal("field_update", update)
        result = await handle.result()
    return result, shipment


async def test_a_normal_delivery_walks_every_status(env):
    result, shipment = await deliver(
        env,
        [
            report("picked_up"),
            report("in_transit"),
            report("out_for_delivery"),
            report("delivered"),
        ],
    )
    assert result.status == "delivered"
    assert shipment.applied == ["picked_up", "in_transit", "out_for_delivery", "delivered"]


async def test_gaps_from_an_offline_device_are_filled_in_order(env):
    _, shipment = await deliver(env, [report("out_for_delivery"), report("delivered")])
    assert shipment.applied == ["picked_up", "in_transit", "out_for_delivery", "delivered"]


async def test_duplicates_and_stale_reports_change_nothing(env):
    same = str(uuid.uuid4())
    _, shipment = await deliver(
        env,
        [
            report("picked_up", event_id=same),
            report("picked_up", event_id=same),  # redelivered
            report("in_transit"),
            report("picked_up"),  # late, from a device that was offline
            report("delivered"),
        ],
    )
    assert shipment.applied == ["picked_up", "in_transit", "out_for_delivery", "delivered"]


async def test_the_last_failed_attempt_starts_a_return(env):
    result, shipment = await deliver(
        env,
        [
            report("delivery_failed", "Nobody home"),
            report("delivery_failed", "Closed"),
            report("delivery_failed", "Refused"),
        ],
        max_attempts=3,
    )
    assert result.status == "return_initiated"
    assert result.delivery_attempts == 3
    assert shipment.applied.count("delivery_failed") == 3
    assert shipment.applied[-1] == "return_initiated"
    assert shipment.reasons["return_initiated"] == "3 failed delivery attempts"


async def test_a_retry_after_a_failure_can_still_deliver(env):
    result, shipment = await deliver(
        env, [report("delivery_failed", "Nobody home"), report("delivered")]
    )
    assert result.status == "delivered" and result.delivery_attempts == 1
    assert shipment.applied[-2:] == ["out_for_delivery", "delivered"]


async def test_an_overdue_pickup_is_reassigned_once(env):
    result, shipment = await deliver(
        env, [report("delivered")], pickup_timeout_seconds=0.5, wait_before=True
    )
    assert shipment.reassigned == 1
    assert result.escalations == 1
    assert result.status == "delivered"
