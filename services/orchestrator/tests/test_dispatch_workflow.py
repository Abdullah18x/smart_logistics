"""DispatchWorkflow against a real Temporal server, with the activities faked."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import pytest
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

from orchestrator.config import Settings
from orchestrator.contracts import (
    AssignCourierInput,
    CourierAssignment,
    DispatchInput,
    EndInput,
    MarkDispatchedInput,
    ShipmentFacts,
    TrackingInput,
)
from orchestrator.temporal import start_dispatch
from orchestrator.workflows.delivery import DeliveryWorkflow
from orchestrator.workflows.dispatch import DispatchWorkflow

COURIER = "c0ffee00-0000-0000-0000-000000000001"


@dataclass
class World:
    """Fake services: records calls, fails on demand."""

    calls: list[str] = field(default_factory=list)
    no_courier_times: int = 0
    transient_label_failures: int = 0
    fail: dict[str, str] = field(default_factory=dict)  # activity -> error type
    stale: bool = False

    def _maybe_fail(self, name: str) -> None:
        if name in self.fail:
            raise ApplicationError(f"{name} said no", type=self.fail[name], non_retryable=True)

    def activities(self) -> list:
        world = self

        @activity.defn(name="validate_shipment")
        async def validate_shipment(shipment_id: str) -> ShipmentFacts:
            world.calls.append("validate_shipment")
            if world.stale:
                raise ApplicationError("old attempt", type="stale_dispatch", non_retryable=True)
            world._maybe_fail("validate_shipment")
            return ShipmentFacts(
                shipment_id=shipment_id,
                reference_no="SL-2026-000001",
                warehouse_id=str(uuid.uuid4()),
                total_weight_g=2100,
                package_count=2,
                destination_city="Karachi",
                items=[("sku-1", 2)],
            )

        @activity.defn(name="confirm_stock")
        async def confirm_stock(facts: ShipmentFacts) -> None:
            world.calls.append("confirm_stock")
            world._maybe_fail("confirm_stock")

        @activity.defn(name="generate_labels")
        async def generate_labels(shipment_id: str) -> int:
            world.calls.append("generate_labels")
            if world.transient_label_failures:
                world.transient_label_failures -= 1
                raise RuntimeError("shipment service restarting")
            return 2

        @activity.defn(name="assign_courier")
        async def assign_courier(request: AssignCourierInput) -> CourierAssignment:
            world.calls.append("assign_courier")
            if world.no_courier_times:
                world.no_courier_times -= 1
                raise ApplicationError(
                    "nobody free", type="no_courier_available", non_retryable=True
                )
            assert request.total_weight_g == 2100
            return CourierAssignment(str(uuid.uuid4()), COURIER, "CR-0001")

        @activity.defn(name="release_courier")
        async def release_courier(request: EndInput) -> None:
            world.calls.append("release_courier")

        @activity.defn(name="start_tracking")
        async def start_tracking(request: TrackingInput) -> None:
            world.calls.append("start_tracking")
            assert request.courier_id == COURIER
            world._maybe_fail("start_tracking")

        @activity.defn(name="cancel_tracking")
        async def cancel_tracking(request: EndInput) -> None:
            world.calls.append("cancel_tracking")

        @activity.defn(name="mark_dispatched")
        async def mark_dispatched(request: MarkDispatchedInput) -> None:
            world.calls.append("mark_dispatched")
            assert request.courier_id == COURIER
            world._maybe_fail("mark_dispatched")

        @activity.defn(name="mark_dispatch_failed")
        async def mark_dispatch_failed(request: EndInput) -> None:
            world.calls.append(f"mark_dispatch_failed:{request.reason}")

        return [
            validate_shipment,
            confirm_stock,
            generate_labels,
            assign_courier,
            release_courier,
            start_tracking,
            cancel_tracking,
            mark_dispatched,
            mark_dispatch_failed,
        ]


async def run_dispatch(env, world: World, **overrides):
    queue = f"test-{uuid.uuid4()}"
    shipment_id = str(uuid.uuid4())
    request = DispatchInput(
        shipment_id=shipment_id,
        attempt=1,
        warehouse_id=str(uuid.uuid4()),
        courier_wait_attempts=overrides.pop("courier_wait_attempts", 3),
        courier_wait_seconds=overrides.pop("courier_wait_seconds", 0.2),
    )
    async with Worker(
        env.client,
        task_queue=queue,
        workflows=[DispatchWorkflow, DeliveryWorkflow],
        activities=world.activities(),
    ):
        return await env.client.execute_workflow(
            DispatchWorkflow.run, request, id=f"dispatch-{shipment_id}-1", task_queue=queue
        )


HAPPY_PATH = [
    "validate_shipment",
    "confirm_stock",
    "generate_labels",
    "assign_courier",
    "start_tracking",
    "mark_dispatched",
]


async def test_every_step_runs_in_order_and_the_shipment_is_dispatched(env):
    world = World()
    result = await run_dispatch(env, world)
    assert result.status == "dispatched"
    assert result.courier_id == COURIER
    assert world.calls == HAPPY_PATH
    assert result.steps == [*HAPPY_PATH, "start_delivery"]


async def test_it_waits_durably_for_a_courier_to_become_free(env):
    world = World(no_courier_times=2)
    result = await run_dispatch(env, world)
    assert result.status == "dispatched"
    assert world.calls.count("assign_courier") == 3


async def test_no_courier_at_all_fails_the_dispatch_without_touching_anything_else(env):
    world = World(no_courier_times=99)
    result = await run_dispatch(env, world, courier_wait_attempts=2)
    assert result.status == "dispatch_failed"
    assert result.reason.startswith("no_courier_available")
    assert "release_courier" not in world.calls  # nothing was assigned
    assert "start_tracking" not in world.calls
    assert world.calls[-1].startswith("mark_dispatch_failed:no_courier_available")


async def test_a_tracking_failure_releases_the_courier(env):
    world = World(fail={"start_tracking": "tracking_refused"})
    result = await run_dispatch(env, world)
    assert result.status == "dispatch_failed"
    tail = world.calls[world.calls.index("start_tracking") + 1 :]
    assert tail[0] == "release_courier"
    assert "cancel_tracking" not in world.calls
    assert tail[-1].startswith("mark_dispatch_failed:tracking_refused")


async def test_a_late_failure_undoes_everything_in_reverse_order(env):
    world = World(fail={"mark_dispatched": "invalid_transition"})
    result = await run_dispatch(env, world)
    assert result.status == "dispatch_failed"
    tail = world.calls[world.calls.index("mark_dispatched") + 1 :]
    assert tail[:2] == ["cancel_tracking", "release_courier"]
    assert result.steps[-2:] == ["undo:cancel_tracking", "undo:release_courier"]


async def test_stock_not_held_fails_before_any_courier_is_booked(env):
    world = World(fail={"confirm_stock": "stock_not_held"})
    result = await run_dispatch(env, world)
    assert result.status == "dispatch_failed"
    assert "assign_courier" not in world.calls


async def test_transient_errors_are_retried_not_failed(env):
    world = World(transient_label_failures=2)
    result = await run_dispatch(env, world)
    assert result.status == "dispatched"
    assert world.calls.count("generate_labels") == 3


async def test_a_stale_attempt_touches_nothing(env):
    world = World(stale=True)
    result = await run_dispatch(env, world)
    assert result.status == "skipped"
    assert world.calls == ["validate_shipment"]


async def test_the_same_attempt_can_never_start_twice(env):
    settings = Settings(task_queue=f"test-{uuid.uuid4()}")
    world = World()
    request = DispatchInput(str(uuid.uuid4()), 1, str(uuid.uuid4()), 1, 0.1)
    workflow_id = f"dispatch-{request.shipment_id}-1"
    async with Worker(
        env.client,
        task_queue=settings.task_queue,
        workflows=[DispatchWorkflow, DeliveryWorkflow],
        activities=world.activities(),
    ):
        assert await start_dispatch(env.client, settings, workflow_id=workflow_id, request=request)
        assert not await start_dispatch(
            env.client, settings, workflow_id=workflow_id, request=request
        )
        await env.client.get_workflow_handle(workflow_id).result()
        # Even after it finished: a redelivered request is still refused.
        assert not await start_dispatch(
            env.client, settings, workflow_id=workflow_id, request=request
        )
    assert world.calls.count("mark_dispatched") == 1


@pytest.mark.parametrize("attempt", [1, 2])
async def test_a_new_attempt_after_failure_does_start(env, attempt):
    settings = Settings(task_queue=f"test-{uuid.uuid4()}")
    request = DispatchInput(str(uuid.uuid4()), attempt, str(uuid.uuid4()), 1, 0.1)
    async with Worker(
        env.client,
        task_queue=settings.task_queue,
        workflows=[DispatchWorkflow, DeliveryWorkflow],
        activities=World().activities(),
    ):
        started = await start_dispatch(
            env.client,
            settings,
            workflow_id=f"dispatch-{request.shipment_id}-{attempt}",
            request=request,
        )
        assert started
        await env.client.get_workflow_handle(f"dispatch-{request.shipment_id}-{attempt}").result()
