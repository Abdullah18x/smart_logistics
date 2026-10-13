"""DispatchWorkflow: a saga across shipment, inventory, courier and tracking.

    validate ─▶ confirm stock ─▶ labels ─▶ assign courier ─▶ start tracking ─▶ dispatched
                                               │                  │
                                    compensate: release courier  close journey
                                               └──────── on failure ──────────▶ dispatch_failed

Each step is an activity: it retries on its own (transient errors), is safe to
repeat (keyed by shipment), and raises a non-retryable error for a business
"no" (no stock held, no courier). When a step fails for good, the steps already
done are undone in reverse and the shipment is marked ``dispatch_failed`` with
the reason; the stock hold is kept so a retry can reuse it.

Temporal persists every step: a worker crash resumes exactly where it stopped.
Workflow code must be deterministic, so all I/O lives in activities and time
comes from ``workflow.sleep``.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError
from temporalio.workflow import ParentClosePolicy

with workflow.unsafe.imports_passed_through():
    from orchestrator.contracts import (
        AssignCourierInput,
        CourierAssignment,
        DeliveryInput,
        DispatchInput,
        DispatchResult,
        EndInput,
        MarkDispatchedInput,
        ShipmentFacts,
        TrackingInput,
    )
    from orchestrator.workflows.delivery import DeliveryWorkflow

#: Transient failures (a service restarting, a timeout) are retried with
#: backoff for up to ~2 minutes before the step counts as failed.
STEP_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=8,
)
#: Compensation must succeed eventually: keep trying much longer.
COMPENSATION_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=1),
    maximum_attempts=50,
)
STEP_TIMEOUT = timedelta(seconds=30)

NO_COURIER = "no_courier_available"
STALE = "stale_dispatch"


def failure_reason(error: BaseException) -> str:
    """A human sentence for the shipment's failure_reason."""
    cause = error.cause if isinstance(error, ActivityError) else error
    if isinstance(cause, ApplicationError):
        return f"{cause.type or 'error'}: {cause.message}"
    return str(cause or error)


def error_type(error: BaseException) -> str | None:
    cause = error.cause if isinstance(error, ActivityError) else error
    return cause.type if isinstance(cause, ApplicationError) else None


@workflow.defn(name="DispatchWorkflow")
class DispatchWorkflow:
    def __init__(self) -> None:
        self._step = "starting"
        self._done: list[str] = []

    @workflow.query
    def progress(self) -> dict:
        """Current step and completed steps, for the orchestrator API and the UI."""
        return {"step": self._step, "done": list(self._done)}

    @workflow.run
    async def run(self, request: DispatchInput) -> DispatchResult:
        sid = request.shipment_id
        compensations: list[tuple[str, object]] = []
        try:
            facts: ShipmentFacts = await self._step_run(
                "validate_shipment", sid, result_type=ShipmentFacts
            )
            await self._step_run("confirm_stock", facts)
            await self._step_run("generate_labels", sid)

            assignment = await self._assign_courier(request, facts)
            compensations.append(("release_courier", EndInput(sid, "dispatch did not complete")))

            await self._step_run(
                "start_tracking",
                TrackingInput(
                    shipment_id=sid,
                    reference_no=facts.reference_no,
                    courier_id=assignment.courier_id,
                    warehouse_id=facts.warehouse_id,
                    destination_city=facts.destination_city,
                ),
            )
            compensations.append(("cancel_tracking", EndInput(sid, "dispatch did not complete")))

            await self._step_run("mark_dispatched", MarkDispatchedInput(sid, assignment.courier_id))
            # Hand over to the delivery workflow, which outlives this one.
            await workflow.start_child_workflow(
                DeliveryWorkflow.run,
                DeliveryInput(
                    shipment_id=sid,
                    courier_id=assignment.courier_id,
                    pickup_timeout_seconds=request.pickup_timeout_seconds,
                    max_attempts=request.max_delivery_attempts,
                ),
                id=f"delivery-{sid}",
                parent_close_policy=ParentClosePolicy.ABANDON,
            )
            self._done.append("start_delivery")
            self._step = "done"
            return DispatchResult(
                shipment_id=sid,
                status="dispatched",
                courier_id=assignment.courier_id,
                steps=list(self._done),
            )
        except ActivityError as error:
            reason = failure_reason(error)
            if error_type(error) == STALE:
                # This attempt is no longer the shipment's current dispatch
                # (redelivered request, or the shipment moved on): touch nothing.
                self._step = "skipped"
                return DispatchResult(sid, "skipped", reason=reason, steps=list(self._done))

            self._step = "compensating"
            workflow.logger.warning("dispatch of %s failed: %s", sid, reason)
            for activity_name, argument in reversed(compensations):
                await workflow.execute_activity(
                    activity_name,
                    argument,
                    start_to_close_timeout=STEP_TIMEOUT,
                    retry_policy=COMPENSATION_RETRY,
                )
                self._done.append(f"undo:{activity_name}")
            await workflow.execute_activity(
                "mark_dispatch_failed",
                EndInput(sid, reason[:500]),
                start_to_close_timeout=STEP_TIMEOUT,
                retry_policy=COMPENSATION_RETRY,
            )
            self._step = "failed"
            return DispatchResult(sid, "dispatch_failed", reason=reason, steps=list(self._done))

    async def _assign_courier(
        self, request: DispatchInput, facts: ShipmentFacts
    ) -> CourierAssignment:
        """Look for a courier; if none is free, wait (durably) and look again."""
        attempts = max(request.courier_wait_attempts, 1)
        for attempt in range(1, attempts + 1):
            try:
                return await self._step_run(
                    "assign_courier",
                    AssignCourierInput(
                        shipment_id=facts.shipment_id,
                        warehouse_id=facts.warehouse_id,
                        total_weight_g=facts.total_weight_g,
                    ),
                    result_type=CourierAssignment,
                )
            except ActivityError as error:
                if error_type(error) != NO_COURIER or attempt == attempts:
                    raise
                self._step = f"waiting for a courier ({attempt}/{attempts})"
                await workflow.sleep(request.courier_wait_seconds)
        raise AssertionError("unreachable")

    async def _step_run(self, name: str, argument: object, *, result_type: type | None = None):
        self._step = name
        result = await workflow.execute_activity(
            name,
            argument,
            start_to_close_timeout=STEP_TIMEOUT,
            retry_policy=STEP_RETRY,
            result_type=result_type,
        )
        self._done.append(name)
        return result
