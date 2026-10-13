"""Courier profiles and availability. Methods flush; the endpoint commits."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from courier.events import CourierEvents, emit
from courier.models import Availability, Courier, CourierStatus
from courier.repository import CourierRepository
from courier.schemas import CourierCreate, CourierUpdate
from sl_platform.auth import Principal
from sl_platform.db import utcnow
from sl_platform.errors import ConflictError, NotFoundError, PermissionDeniedError
from sl_platform.roles import UserRole

#: Who may read courier profiles besides the courier themself.
STAFF_ROLES = frozenset(
    {UserRole.ADMIN, UserRole.CUSTOMER_SUPPORT, UserRole.WAREHOUSE_OPERATOR, UserRole.SYSTEM}
)


class CourierBusyError(ConflictError):
    """The courier still carries shipments."""

    code = "courier_has_active_assignments"


class CourierService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.couriers = CourierRepository(session)

    async def get(self, courier_id: uuid.UUID, principal: Principal) -> Courier:
        courier = await self.couriers.get(courier_id)
        if courier is None or not self._may_see(principal, courier):
            raise NotFoundError("Courier not found.")
        return courier

    async def mine(self, principal: Principal) -> Courier:
        if principal.courier_id is None:
            raise NotFoundError("This account has no courier profile.")
        return await self.get(principal.courier_id, principal)

    async def create(self, data: CourierCreate) -> Courier:
        courier = Courier(id=uuid.uuid4(), **data.model_dump())
        self.session.add(courier)
        await self.session.flush()
        emit(self.session, CourierEvents.CREATED, courier)
        return courier

    async def update(self, courier_id: uuid.UUID, data: CourierUpdate) -> Courier:
        courier = await self._require(courier_id)
        changes = data.model_dump(exclude_unset=True)
        for field, value in changes.items():
            setattr(courier, field, value)
        if changes:
            await self.session.flush()
            emit(self.session, CourierEvents.UPDATED, courier, changed=sorted(changes))
        return courier

    async def set_status(
        self, courier_id: uuid.UUID, status: CourierStatus, reason: str | None
    ) -> Courier:
        courier = await self._require(courier_id)
        if courier.status is status:
            return courier
        courier.status = status
        if status is CourierStatus.SUSPENDED:
            # A suspended partner takes no new work; current jobs stay until reassigned.
            courier.availability = Availability.OFFLINE
            courier.availability_changed_at = utcnow()
        await self.session.flush()
        emit(self.session, CourierEvents.STATUS_CHANGED, courier, reason=reason)
        return courier

    async def set_availability(
        self, courier_id: uuid.UUID, availability: Availability, principal: Principal
    ) -> Courier:
        courier = await self._require(courier_id)
        if principal.role is UserRole.COURIER and principal.courier_id != courier.id:
            raise NotFoundError("Courier not found.")
        if availability is not Availability.OFFLINE and courier.status is not CourierStatus.ACTIVE:
            raise PermissionDeniedError("A suspended courier cannot go on shift.")
        if courier.availability is availability:
            return courier
        courier.availability = availability
        courier.availability_changed_at = utcnow()
        await self.session.flush()
        emit(self.session, CourierEvents.AVAILABILITY_CHANGED, courier)
        return courier

    async def delete(self, courier_id: uuid.UUID, actor_id: uuid.UUID) -> None:
        courier = await self._require(courier_id)
        if courier.active_assignments:
            raise CourierBusyError(
                f"{courier.code} still carries {courier.active_assignments} shipment(s); "
                "reassign them first."
            )
        courier.deleted_at = utcnow()
        courier.deleted_by = actor_id
        courier.availability = Availability.OFFLINE
        await self.session.flush()
        emit(self.session, CourierEvents.DELETED, courier)

    async def restore(self, courier_id: uuid.UUID) -> Courier:
        courier = await self.couriers.get(courier_id, include_deleted=True)
        if courier is None:
            raise NotFoundError("Courier not found.")
        if courier.deleted_at is None:
            return courier
        courier.deleted_at = None
        courier.deleted_by = None
        await self.session.flush()
        emit(self.session, CourierEvents.RESTORED, courier)
        return courier

    async def _require(self, courier_id: uuid.UUID) -> Courier:
        courier = await self.couriers.get(courier_id)
        if courier is None:
            raise NotFoundError("Courier not found.")
        return courier

    @staticmethod
    def _may_see(principal: Principal, courier: Courier) -> bool:
        return principal.role in STAFF_ROLES or principal.courier_id == courier.id
