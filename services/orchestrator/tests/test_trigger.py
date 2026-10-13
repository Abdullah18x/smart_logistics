"""dispatch_requested events start exactly one workflow per attempt."""

from __future__ import annotations

import uuid

from temporalio.worker import Worker

from orchestrator.config import Settings
from orchestrator.trigger import handle
from orchestrator.workflows.delivery import DeliveryWorkflow
from orchestrator.workflows.dispatch import DispatchWorkflow
from sl_platform.events import EventEnvelope
from tests.test_dispatch_workflow import World


def requested(shipment_id: str, attempt: int = 1) -> EventEnvelope:
    return EventEnvelope(
        event_type="shipment.dispatch_requested",
        producer="shipment",
        key=shipment_id,
        payload={
            "id": shipment_id,
            "attempt": attempt,
            "workflow_id": f"dispatch-{shipment_id}-{attempt}",
            "origin_warehouse_id": str(uuid.uuid4()),
        },
    )


async def test_a_request_starts_the_workflow_and_a_redelivery_does_not(env):
    settings = Settings(task_queue=f"test-{uuid.uuid4()}", courier_wait_seconds=0)
    world = World()
    shipment_id = str(uuid.uuid4())
    async with Worker(
        env.client,
        task_queue=settings.task_queue,
        workflows=[DispatchWorkflow, DeliveryWorkflow],
        activities=world.activities(),
    ):
        assert await handle(env.client, settings, requested(shipment_id))
        assert not await handle(env.client, settings, requested(shipment_id))
        result = await env.client.get_workflow_handle(f"dispatch-{shipment_id}-1").result()
    assert result["status"] == "dispatched"


async def test_other_shipment_events_are_ignored(env):
    event = EventEnvelope(event_type="shipment.created", producer="shipment", key="x", payload={})
    assert not await handle(env.client, Settings(), event)


def milestone_event(shipment_id: str, milestone: str, source: str = "courier") -> EventEnvelope:
    return EventEnvelope(
        event_type="tracking.milestone_recorded",
        producer="tracking",
        key=shipment_id,
        payload={
            "shipment_id": shipment_id,
            "milestone": milestone,
            "occurred_at": "2026-10-12T10:00:00+00:00",
            "source": source,
        },
    )


async def test_courier_milestones_reach_the_delivery_workflow(env):
    from orchestrator.contracts import DeliveryInput
    from tests.test_delivery_workflow import Shipment

    shipment = Shipment()
    queue = f"test-{uuid.uuid4()}"
    shipment_id = str(uuid.uuid4())
    async with Worker(
        env.client,
        task_queue=queue,
        workflows=[DeliveryWorkflow],
        activities=shipment.activities(),
    ):
        handle = await env.client.start_workflow(
            DeliveryWorkflow.run,
            DeliveryInput(shipment_id, str(uuid.uuid4())),
            id=f"delivery-{shipment_id}",
            task_queue=queue,
        )
        # Mirrored from the shipment service: not forwarded (it would loop).
        assert not await handle_event(env, milestone_event(shipment_id, "picked_up", "shipment"))
        assert await handle_event(env, milestone_event(shipment_id, "delivered"))
        result = await handle.result()
    assert result.status == "delivered"
    assert shipment.applied[-1] == "delivered"


async def test_a_milestone_without_a_workflow_is_skipped(env):
    assert not await handle_event(env, milestone_event(str(uuid.uuid4()), "picked_up"))


async def handle_event(env, event: EventEnvelope) -> bool:
    return await handle(env.client, Settings(), event)
