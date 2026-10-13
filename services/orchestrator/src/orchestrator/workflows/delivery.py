"""DeliveryWorkflow: one per dispatched shipment, open until it is delivered or
handed to returns.

Couriers report from the field to the tracking service; tracking forwards each
recorded milestone here as a ``field_update`` signal. The workflow is the only
writer of post-dispatch shipment status, so every report is checked before it
reaches the shipment:

* **duplicates** (the same ``event_id`` twice) are ignored;
* **stale** reports (``picked_up`` arriving after ``out_for_delivery``) are ignored;
* **gaps** are filled: if a device that was offline reports ``out_for_delivery``
  straight after ``dispatched``, ``picked_up`` and ``in_transit`` are applied
  first, so the shipment walks the state machine legally;
* after the **last allowed failed attempt** a return is started automatically.

Timers make it an escalation engine: if nobody picks the shipment up in time,
it is reassigned to another courier (once), and the wait starts again.
"""

from __future__ import annotations

from collections import deque
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from orchestrator.contracts import (
        ApplyStatusInput,
        DeliveryInput,
        DeliveryResult,
        FieldUpdate,
        ReassignInput,
    )

RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=1),
    maximum_attempts=30,
)
TIMEOUT = timedelta(seconds=30)

#: The forward path a shipment walks before an attempt.
PATH = ("dispatched", "picked_up", "in_transit", "out_for_delivery")
TERMINAL = frozenset({"delivered", "return_initiated"})


def steps_towards(current: str, reported: str) -> list[str]:
    """The statuses to apply, in order, to honour a field report. [] = ignore."""
    position = PATH.index(current) if current in PATH else None
    if reported in PATH:
        target = PATH.index(reported)
        if current == "delivery_failed" and reported == "out_for_delivery":
            return ["out_for_delivery"]  # the next attempt
        if position is None or target <= position:
            return []  # stale or repeated
        return list(PATH[position + 1 : target + 1])
    if reported in ("delivered", "delivery_failed"):
        if current == "delivery_failed":
            return ["out_for_delivery", reported]  # a new attempt, implicitly
        if position is None:
            return []
        return [*PATH[position + 1 :], reported]
    return []


@workflow.defn(name="DeliveryWorkflow")
class DeliveryWorkflow:
    def __init__(self) -> None:
        self._pending: deque[FieldUpdate] = deque()
        self._seen: set[str] = set()
        self._status = "dispatched"
        self._attempts = 0
        self._escalations = 0
        self._applied: list[str] = []

    @workflow.signal(name="field_update")
    def field_update(self, update: FieldUpdate) -> None:
        self._pending.append(update)

    @workflow.query(name="state")
    def state(self) -> dict:
        return {
            "status": self._status,
            "delivery_attempts": self._attempts,
            "escalations": self._escalations,
            "applied": list(self._applied),
        }

    @workflow.run
    async def run(self, request: DeliveryInput) -> DeliveryResult:
        sid = request.shipment_id
        while self._status not in TERMINAL:
            awaiting_pickup = self._status == "dispatched" and self._escalations == 0
            if awaiting_pickup:
                try:
                    await workflow.wait_condition(
                        lambda: bool(self._pending), timeout=request.pickup_timeout_seconds
                    )
                except TimeoutError:
                    await self._escalate(sid)
                    continue
            else:
                await workflow.wait_condition(lambda: bool(self._pending))

            update = self._pending.popleft()
            if update.event_id in self._seen:
                continue
            self._seen.add(update.event_id)

            for status in steps_towards(self._status, update.milestone):
                reason = update.failure_reason if status == "delivery_failed" else None
                await self._apply(sid, status, reason)
                if status == "delivery_failed":
                    self._attempts += 1
                    if self._attempts >= request.max_attempts:
                        await self._apply(
                            sid,
                            "return_initiated",
                            f"{self._attempts} failed delivery attempts",
                        )
                        break

        return DeliveryResult(
            shipment_id=sid,
            status=self._status,
            delivery_attempts=self._attempts,
            escalations=self._escalations,
            applied=list(self._applied),
        )

    async def _apply(self, sid: str, status: str, reason: str | None) -> None:
        await workflow.execute_activity(
            "apply_delivery_status",
            ApplyStatusInput(sid, status, reason),
            start_to_close_timeout=TIMEOUT,
            retry_policy=RETRY,
        )
        self._status = status
        self._applied.append(status)

    async def _escalate(self, sid: str) -> None:
        self._escalations += 1
        workflow.logger.warning("pickup of %s is overdue; reassigning", sid)
        await workflow.execute_activity(
            "reassign_overdue_pickup",
            ReassignInput(sid, "Pickup overdue: reassigned automatically"),
            start_to_close_timeout=TIMEOUT,
            retry_policy=RETRY,
        )
