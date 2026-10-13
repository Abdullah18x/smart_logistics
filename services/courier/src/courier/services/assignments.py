"""Assigning couriers to shipments. Methods flush; the endpoint commits.

Every change locks the rows it touches (the active assignment, then couriers in
id order) and adjusts the courier's load in the same transaction, so load never
drifts from the assignments behind it and two transactions never deadlock.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from courier.events import CourierEvents, emit_assignment
from courier.models import (
    AssignmentMethod,
    AssignmentStatus,
    Availability,
    Courier,
    CourierAssignment,
    CourierStatus,
)
from courier.repository import AssignmentRepository, CourierRepository
from courier.schemas import AssignmentRequest
from courier.services.replicas import require_warehouse
from sl_platform.db import utcnow
from sl_platform.errors import ConflictError, NotFoundError


class NoCourierAvailableError(ConflictError):
    """No active, available courier with spare capacity serves this city."""

    code = "no_courier_available"


class CourierUnavailableError(ConflictError):
    """The chosen courier cannot take this shipment right now."""

    code = "courier_unavailable"


class AlreadyAssignedError(ConflictError):
    """The shipment already has a different courier; reassign instead."""

    code = "already_assigned"


@dataclass(frozen=True)
class AssignmentOutcome:
    assignment: CourierAssignment
    courier: Courier
    created: bool


class AssignmentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.couriers = CourierRepository(session)
        self.assignments = AssignmentRepository(session)

    async def assign(
        self, request: AssignmentRequest, *, actor_id: uuid.UUID | None
    ) -> AssignmentOutcome:
        """Assign a courier, or return the existing assignment (safe to retry)."""
        existing = await self.assignments.active_for_shipment(request.shipment_id, lock=True)
        if existing is not None:
            if request.courier_id not in (None, existing.courier_id):
                raise AlreadyAssignedError(
                    "This shipment already has a courier; use reassign to change it."
                )
            courier = await self.couriers.get(existing.courier_id, include_deleted=True)
            return AssignmentOutcome(existing, courier, created=False)

        warehouse = await require_warehouse(self.session, request.warehouse_id)
        if request.courier_id is not None:
            courier = (await self.couriers.lock([request.courier_id])).get(request.courier_id)
            self._check_can_take(courier, request.total_weight_g)
            method = AssignmentMethod.MANUAL
        else:
            courier = await self.couriers.pick_assignable(
                city=warehouse.city,
                warehouse_id=warehouse.id,
                weight_g=request.total_weight_g,
            )
            if courier is None:
                raise NoCourierAvailableError(
                    f"No available courier with spare capacity in {warehouse.city}."
                )
            method = AssignmentMethod.AUTOMATIC

        assignment = await self._start(
            courier,
            shipment_id=request.shipment_id,
            warehouse_id=request.warehouse_id,
            method=method,
            actor_id=actor_id,
            reason=request.reason,
        )
        return AssignmentOutcome(assignment, courier, created=True)

    async def reassign(
        self,
        shipment_id: uuid.UUID,
        *,
        courier_id: uuid.UUID | None,
        reason: str,
        actor_id: uuid.UUID | None,
    ) -> AssignmentOutcome:
        """Hand the shipment to another courier: the manual override."""
        current = await self.assignments.active_for_shipment(shipment_id, lock=True)
        if current is None:
            raise NotFoundError("This shipment has no active courier to reassign.")
        if courier_id == current.courier_id:
            raise ConflictError("That courier already carries this shipment.")

        warehouse = await require_warehouse(self.session, current.warehouse_id)
        if courier_id is not None:
            locked = await self.couriers.lock([current.courier_id, courier_id])
            new = locked.get(courier_id)
            self._check_can_take(new, None)
            method = AssignmentMethod.MANUAL
        else:
            locked = await self.couriers.lock([current.courier_id])
            new = await self.couriers.pick_assignable(
                city=warehouse.city,
                warehouse_id=warehouse.id,
                weight_g=None,
                exclude=[current.courier_id],
            )
            if new is None:
                raise NoCourierAvailableError(
                    f"No other available courier with spare capacity in {warehouse.city}."
                )
            method = AssignmentMethod.AUTOMATIC

        self._finish(current, locked[current.courier_id], completed=False, reason=reason)
        # The partial unique index allows one active row: end the old one first.
        await self.session.flush()
        emit_assignment(self.session, CourierEvents.UNASSIGNED, current)
        assignment = await self._start(
            new,
            shipment_id=shipment_id,
            warehouse_id=current.warehouse_id,
            method=method,
            actor_id=actor_id,
            reason=reason,
        )
        return AssignmentOutcome(assignment, new, created=True)

    async def end(
        self, shipment_id: uuid.UUID, *, completed: bool, reason: str | None
    ) -> CourierAssignment | None:
        """Complete or release the active assignment. A no-op if there is none."""
        current = await self.assignments.active_for_shipment(shipment_id, lock=True)
        if current is None:
            return None
        courier = (await self.couriers.lock([current.courier_id]))[current.courier_id]
        self._finish(current, courier, completed=completed, reason=reason)
        await self.session.flush()
        emit_assignment(self.session, CourierEvents.UNASSIGNED, current)
        return current

    # --- internals ---------------------------------------------------------------

    async def _start(
        self,
        courier: Courier,
        *,
        shipment_id: uuid.UUID,
        warehouse_id: uuid.UUID,
        method: AssignmentMethod,
        actor_id: uuid.UUID | None,
        reason: str | None,
    ) -> CourierAssignment:
        now = utcnow()
        assignment = CourierAssignment(
            id=uuid.uuid4(),
            courier_id=courier.id,
            shipment_id=shipment_id,
            warehouse_id=warehouse_id,
            status=AssignmentStatus.ACTIVE,
            method=method,
            assigned_by=actor_id,
            reason=reason,
            assigned_at=now,
        )
        self.session.add(assignment)
        courier.active_assignments += 1
        courier.last_assigned_at = now
        await self.session.flush()
        emit_assignment(self.session, CourierEvents.ASSIGNED, assignment)
        return assignment

    @staticmethod
    def _finish(
        assignment: CourierAssignment, courier: Courier, *, completed: bool, reason: str | None
    ) -> None:
        assignment.status = AssignmentStatus.COMPLETED if completed else AssignmentStatus.RELEASED
        assignment.ended_at = utcnow()
        assignment.end_reason = reason
        courier.active_assignments = max(courier.active_assignments - 1, 0)

    @staticmethod
    def _check_can_take(courier: Courier | None, weight_g: int | None) -> None:
        if courier is None or courier.deleted_at is not None:
            raise NotFoundError("Courier not found.")
        if courier.status is not CourierStatus.ACTIVE:
            raise CourierUnavailableError(f"{courier.code} is suspended.")
        if courier.availability is not Availability.AVAILABLE:
            raise CourierUnavailableError(f"{courier.code} is {courier.availability.value}.")
        if courier.active_assignments >= courier.max_active_shipments:
            raise CourierUnavailableError(f"{courier.code} is at capacity.")
        if weight_g is not None and courier.max_weight_g and weight_g > courier.max_weight_g:
            raise CourierUnavailableError(f"{courier.code} cannot carry {weight_g} g.")
