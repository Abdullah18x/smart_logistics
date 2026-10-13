"""Dispatch activities: the workflow's only way to touch the world.

Each one calls a service over HTTP as the orchestrator's service account
(``system`` token from Identity) and is safe to repeat:

* a business "no" from a service (4xx) becomes a **non-retryable**
  ``ApplicationError`` whose ``type`` is the service's error code;
* anything transient (timeouts, 5xx, ``concurrent_update``) is raised as is, so
  Temporal retries the activity with backoff;
* outcomes that already happened (labels re-printed, a hold already confirmed,
  the shipment already marked) count as success.
"""

from __future__ import annotations

from temporalio import activity
from temporalio.exceptions import ApplicationError

from orchestrator.contracts import (
    ApplyStatusInput,
    AssignCourierInput,
    CourierAssignment,
    EndInput,
    MarkDispatchedInput,
    ReassignInput,
    ShipmentFacts,
    TrackingInput,
)
from sl_platform.errors import RemoteError
from sl_platform.http import ServiceClient

#: Remote 4xx codes that mean "try again shortly", not "no".
RETRYABLE_CODES = frozenset({"concurrent_update", "request_in_progress"})


def business_error(error: RemoteError) -> Exception:
    if error.status_code >= 500 or error.code in RETRYABLE_CODES:
        return error  # Temporal retries plain exceptions
    return ApplicationError(str(error), type=error.code, non_retryable=True)


class DispatchActivities:
    def __init__(
        self,
        *,
        shipment: ServiceClient,
        inventory: ServiceClient,
        courier: ServiceClient,
        tracking: ServiceClient,
    ) -> None:
        self.shipment = shipment
        self.inventory = inventory
        self.courier = courier
        self.tracking = tracking

    def all(self) -> list:
        return [
            self.validate_shipment,
            self.confirm_stock,
            self.generate_labels,
            self.assign_courier,
            self.release_courier,
            self.start_tracking,
            self.cancel_tracking,
            self.mark_dispatched,
            self.mark_dispatch_failed,
            self.apply_delivery_status,
            self.reassign_overdue_pickup,
        ]

    # --- steps ---------------------------------------------------------------------

    @activity.defn(name="validate_shipment")
    async def validate_shipment(self, shipment_id: str) -> ShipmentFacts:
        shipment = await self._get_shipment(shipment_id)
        workflow_id = activity.info().workflow_id
        if (
            shipment["status"] != "dispatching"
            or shipment.get("dispatch_workflow_id") != workflow_id
        ):
            raise ApplicationError(
                f"Shipment is {shipment['status']} with dispatch "
                f"{shipment.get('dispatch_workflow_id')}; this run is {workflow_id}.",
                type="stale_dispatch",
                non_retryable=True,
            )
        problems = []
        if not shipment["packages"]:
            problems.append("no packages")
        if any(p["weight_g"] <= 0 for p in shipment["packages"]):
            problems.append("a package has no weight")
        address = shipment.get("destination_address") or {}
        if not (address.get("line1") and address.get("city") and address.get("contact_phone")):
            problems.append("incomplete destination address")
        if not shipment["items"]:
            problems.append("no items")
        if problems:
            raise ApplicationError(
                "Shipment is not ready: " + ", ".join(problems) + ".",
                type="invalid_shipment",
                non_retryable=True,
            )
        return ShipmentFacts(
            shipment_id=shipment_id,
            reference_no=shipment["reference_no"],
            warehouse_id=shipment["origin_warehouse_id"],
            total_weight_g=sum(p["weight_g"] for p in shipment["packages"]),
            package_count=len(shipment["packages"]),
            destination_city=address["city"],
            items=[(i["sku_id"], i["quantity"]) for i in shipment["items"]],
        )

    @activity.defn(name="confirm_stock")
    async def confirm_stock(self, facts: ShipmentFacts) -> None:
        """Confirm the hold (normally already done by ``shipment.created``) and
        check every line is still held in full."""
        sid = facts.shipment_id
        try:
            await self.inventory.post(f"/api/v1/inventory/reservations/{sid}/confirm")
            holds = (
                await self.inventory.get(
                    "/api/v1/inventory/reservations",
                    params={"shipment_id": sid, "limit": 200},
                )
            ).json()
        except RemoteError as error:
            raise business_error(error) from error
        held = {h["sku_id"]: h["quantity"] for h in holds if h["status"] == "held"}
        missing = [sku for sku, qty in facts.items if held.get(sku) != qty]
        if missing:
            raise ApplicationError(
                f"Stock is not held for {len(missing)} line(s).",
                type="stock_not_held",
                non_retryable=True,
            )

    @activity.defn(name="generate_labels")
    async def generate_labels(self, shipment_id: str) -> int:
        try:
            shipment = (await self.shipment.post(f"/api/v1/shipments/{shipment_id}/labels")).json()
        except RemoteError as error:
            raise business_error(error) from error
        return len(shipment["packages"])

    @activity.defn(name="assign_courier")
    async def assign_courier(self, request: AssignCourierInput) -> CourierAssignment:
        try:
            body = (
                await self.courier.post(
                    "/api/v1/couriers/assignments",
                    json={
                        "shipment_id": request.shipment_id,
                        "warehouse_id": request.warehouse_id,
                        "total_weight_g": request.total_weight_g,
                        "reason": "dispatch",
                    },
                )
            ).json()
        except RemoteError as error:
            raise business_error(error) from error
        return CourierAssignment(
            assignment_id=body["assignment"]["id"],
            courier_id=body["courier"]["id"],
            courier_code=body["courier"]["code"],
        )

    @activity.defn(name="start_tracking")
    async def start_tracking(self, request: TrackingInput) -> None:
        try:
            await self.tracking.post(
                "/api/v1/tracking/journeys",
                json={
                    "shipment_id": request.shipment_id,
                    "reference_no": request.reference_no,
                    "courier_id": request.courier_id,
                    "origin_warehouse_id": request.warehouse_id,
                    "destination_city": request.destination_city,
                },
            )
        except RemoteError as error:
            raise business_error(error) from error

    @activity.defn(name="mark_dispatched")
    async def mark_dispatched(self, request: MarkDispatchedInput) -> None:
        await self._move(
            request.shipment_id, "dispatched", courier_id=request.courier_id, reason=None
        )

    # --- compensation and failure ------------------------------------------------------

    @activity.defn(name="release_courier")
    async def release_courier(self, request: EndInput) -> None:
        await self.courier.post(
            f"/api/v1/couriers/assignments/{request.shipment_id}/release",
            json={"reason": request.reason},
        )

    @activity.defn(name="cancel_tracking")
    async def cancel_tracking(self, request: EndInput) -> None:
        try:
            await self.tracking.post(
                f"/api/v1/tracking/journeys/{request.shipment_id}/cancel",
                json={"reason": request.reason},
            )
        except RemoteError as error:
            if error.status_code == 404:
                return  # never started: nothing to undo
            raise

    @activity.defn(name="mark_dispatch_failed")
    async def mark_dispatch_failed(self, request: EndInput) -> None:
        await self._move(request.shipment_id, "dispatch_failed", reason=request.reason)

    # --- delivery ------------------------------------------------------------------------

    @activity.defn(name="apply_delivery_status")
    async def apply_delivery_status(self, request: ApplyStatusInput) -> None:
        await self._move(request.shipment_id, request.status, reason=request.reason)

    @activity.defn(name="reassign_overdue_pickup")
    async def reassign_overdue_pickup(self, request: ReassignInput) -> bool:
        """Best effort: if no other courier is free, keep the current one."""
        try:
            await self.courier.post(
                f"/api/v1/couriers/assignments/{request.shipment_id}/reassign",
                json={"reason": request.reason},
            )
        except RemoteError as error:
            if error.status_code >= 500 or error.code in RETRYABLE_CODES:
                raise
            activity.logger.warning("could not reassign %s: %s", request.shipment_id, error.code)
            return False
        return True

    # --- helpers -----------------------------------------------------------------------

    async def _get_shipment(self, shipment_id: str) -> dict:
        try:
            return (await self.shipment.get(f"/api/v1/shipments/{shipment_id}")).json()
        except RemoteError as error:
            raise business_error(error) from error

    async def _move(
        self, shipment_id: str, status: str, *, reason: str | None, courier_id: str | None = None
    ) -> None:
        body: dict = {"status": status, "reason": reason}
        if courier_id:
            body["courier_id"] = courier_id
        try:
            await self.shipment.patch(f"/api/v1/shipments/{shipment_id}/status", json=body)
        except RemoteError as error:
            if error.status_code == 409 and error.code not in RETRYABLE_CODES:
                # A retry after a lost response: fine if it already happened.
                current = await self._get_shipment(shipment_id)
                if current["status"] == status:
                    return
            raise business_error(error) from error
