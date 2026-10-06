"""Shipment lifecycle. Methods flush; the endpoint commits once."""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from shipment.clients import InventoryClient
from shipment.events import ShipmentEvents, emit, emit_created
from shipment.models import (
    Address,
    Package,
    PackageStatus,
    ServiceLevel,
    Shipment,
    ShipmentItem,
    ShipmentStatus,
    ShipmentStatusHistory,
)
from shipment.repository import ShipmentRepository
from shipment.schemas import PackageCreate, PackageUpdate, ShipmentCreate, ShipmentUpdate
from shipment.services.replicas import require_operational
from shipment.state_machine import EDITABLE_STATES, TRANSITION_ROLES, assert_transition
from sl_platform.auth import Principal
from sl_platform.errors import ConflictError, NotFoundError, PermissionDeniedError
from sl_platform.roles import UserRole

logger = logging.getLogger("shipment.service")

PACKAGE_BARCODE_PREFIX = "PKG"
SERVICE_LEVEL_WINDOWS: dict[ServiceLevel, timedelta] = {
    ServiceLevel.SAME_DAY: timedelta(hours=8),
    ServiceLevel.EXPRESS: timedelta(days=1),
    ServiceLevel.STANDARD: timedelta(days=3),
    ServiceLevel.ECONOMY: timedelta(days=5),
}
#: Deterministic shipment ids for idempotent creates (see ``shipment_id_for``).
SHIPMENT_ID_NAMESPACE = uuid.UUID("2f0c3c1e-6a8b-4c1d-9b57-3d1f0a7e5c42")


def shipment_id_for(owner: uuid.UUID, idempotency_key: str | None) -> uuid.UUID:
    """With an Idempotency-Key, a retried create reuses the same shipment id, so
    Inventory hands back the hold it already took instead of taking a second."""
    if idempotency_key:
        return uuid.uuid5(SHIPMENT_ID_NAMESPACE, f"{owner}:{idempotency_key}")
    return uuid.uuid4()


class ShipmentService:
    def __init__(self, session: AsyncSession, inventory: InventoryClient) -> None:
        self.session = session
        self.inventory = inventory
        self.repo = ShipmentRepository(session)
        self._held_for: uuid.UUID | None = None

    # --- reads ---------------------------------------------------------------------

    async def get(self, shipment_id: uuid.UUID, *, principal: Principal) -> Shipment:
        shipment = await self.repo.get_with_detail(shipment_id)
        if shipment is None or not self._visible(shipment, principal):
            raise NotFoundError("Shipment not found.")
        return shipment

    async def list(self, *, principal: Principal, **filters) -> tuple[list[Shipment], int]:
        if principal.role is UserRole.COURIER:
            if principal.courier_id is None:
                return [], 0  # no fleet profile, so nothing can be assigned to them
            return await self.repo.list_shipments(
                restrict_to_courier_id=principal.courier_id, **filters
            )
        return await self.repo.list_shipments(
            allowed_warehouse_ids=principal.scoped_warehouse_ids, **filters
        )

    async def history(
        self, shipment_id: uuid.UUID, *, principal: Principal
    ) -> list[ShipmentStatusHistory]:
        await self.get(shipment_id, principal=principal)
        return await self.repo.history(shipment_id)

    # --- create --------------------------------------------------------------------

    async def create(
        self, payload: ShipmentCreate, *, principal: Principal, shipment_id: uuid.UUID
    ) -> Shipment:
        """Reserve, then record.

        1. Check the origin warehouse against the local replica (no call).
        2. Hold the stock with Inventory — the one synchronous call. A shortfall
           comes back as Inventory's own 409 ``insufficient_stock``.
        3. Insert the shipment, its items (from the SKU snapshots Inventory
           returned), parcels, the first history row and ``shipment.created``.

        If step 3 fails, ``compensate`` releases the hold. If even that fails,
        the hold was never confirmed and Inventory reclaims it on expiry.
        """
        warehouse = await require_operational(self.session, payload.origin_warehouse_id)
        reservation = await self.inventory.reserve(
            token=principal.token,
            shipment_id=shipment_id,
            warehouse_id=payload.origin_warehouse_id,
            lines=[(item.sku_id, item.quantity) for item in payload.items],
        )
        self._held_for = shipment_id

        address = Address(**payload.destination_address.model_dump())
        self.session.add(address)
        await self.session.flush()

        shipment = Shipment(
            id=shipment_id,
            reference_no=await self.repo.next_reference_no(),
            customer_reference=payload.customer_reference,
            status=ShipmentStatus.CREATED,
            service_level=payload.service_level,
            priority=payload.priority,
            origin_warehouse_id=warehouse.id,
            origin_warehouse_code=warehouse.code,
            created_by=principal.user_id,
            destination_address_id=address.id,
            declared_value=payload.declared_value,
            currency=payload.currency,
            special_instructions=payload.special_instructions,
            promised_delivery_at=payload.promised_delivery_at
            or datetime.now(UTC) + SERVICE_LEVEL_WINDOWS[payload.service_level],
            total_weight_g=sum(line.unit_weight_g * line.quantity for line in reservation.lines),
        )
        self.session.add(shipment)
        await self.session.flush()

        for line in reservation.lines:
            self.session.add(
                ShipmentItem(
                    shipment_id=shipment.id,
                    sku_id=line.sku_id,
                    quantity=line.quantity,
                    sku_code=line.sku_code,
                    sku_name=line.sku_name,
                    unit_weight_g=line.unit_weight_g,
                    unit_value=line.unit_value,
                )
            )
        for index, parcel in enumerate(payload.packages, start=1):
            self.session.add(
                Package(
                    shipment_id=shipment.id,
                    sequence_no=index,
                    barcode=self._barcode(),
                    **parcel.model_dump(),
                )
            )
        self._record(shipment, None, ShipmentStatus.CREATED, principal, None)
        await self.session.flush()

        created = await self.repo.get_with_detail(shipment.id)
        emit_created(self.session, created)
        return created

    async def compensate(self, principal: Principal) -> None:
        """Undo the stock hold of a create that did not commit. Best effort."""
        if self._held_for is None:
            return
        shipment_id, self._held_for = self._held_for, None
        try:
            await self.inventory.release(
                token=principal.token,
                shipment_id=shipment_id,
                reason="Shipment could not be recorded",
            )
        except Exception:
            logger.warning("could not release hold for %s; it will expire unconfirmed", shipment_id)

    def creation_committed(self) -> None:
        self._held_for = None

    # --- update ----------------------------------------------------------------------

    async def update(
        self, shipment_id: uuid.UUID, payload: ShipmentUpdate, *, principal: Principal
    ) -> Shipment:
        shipment = await self.get(shipment_id, principal=principal)
        if shipment.status not in EDITABLE_STATES:
            raise ConflictError(f"A shipment in {shipment.status.value} can no longer be edited.")
        changes = payload.model_dump(exclude_unset=True)
        for required in ("service_level", "priority"):
            if required in changes and changes[required] is None:
                raise ConflictError(f"{required} cannot be cleared.")
        for field, value in changes.items():
            setattr(shipment, field, value)
        # A new service level moves the promise, unless the caller set one.
        if "service_level" in changes and "promised_delivery_at" not in changes:
            shipment.promised_delivery_at = (
                shipment.created_at + SERVICE_LEVEL_WINDOWS[shipment.service_level]
            )
        await self.session.flush()
        emit(self.session, ShipmentEvents.UPDATED, shipment, changed=sorted(changes))
        return await self.get(shipment_id, principal=principal)

    # --- lifecycle -------------------------------------------------------------------

    async def change_status(
        self,
        shipment_id: uuid.UUID,
        target: ShipmentStatus,
        *,
        principal: Principal,
        reason: str | None = None,
        expected_version: int | None = None,
    ) -> Shipment:
        shipment = await self.get(shipment_id, principal=principal)
        if principal.role not in TRANSITION_ROLES.get(target, frozenset()):
            raise PermissionDeniedError(f"Your role cannot move a shipment to {target.value}.")
        if principal.role is UserRole.WAREHOUSE_OPERATOR and not principal.may_touch_warehouse(
            shipment.origin_warehouse_id
        ):
            raise PermissionDeniedError("This shipment is not from one of your warehouses.")
        if expected_version is not None and expected_version != shipment.version:
            from sl_platform.errors import ConcurrentUpdateError

            raise ConcurrentUpdateError()
        assert_transition(shipment.status, target)

        previous = shipment.status
        now = datetime.now(UTC)
        shipment.status = target
        if target is ShipmentStatus.DISPATCHED:
            shipment.dispatched_at = now
        elif target is ShipmentStatus.PICKED_UP:
            shipment.picked_up_at = now
        elif target is ShipmentStatus.DELIVERED:
            shipment.delivered_at = now
        elif target is ShipmentStatus.CANCELLED:
            shipment.cancelled_at = now
            shipment.failure_reason = reason
        elif target is ShipmentStatus.DELIVERY_FAILED:
            shipment.delivery_attempt_count += 1
            shipment.failure_reason = reason
        self._record(shipment, previous, target, principal, reason)
        # The UPDATE carries WHERE version = <read>; a concurrent change makes it
        # match nothing and SQLAlchemy raises StaleDataError -> 409.
        await self.session.flush()
        # Stock follows from the event: Inventory commits on dispatched and
        # releases on cancelled (the Phase 2 saga will call it synchronously).
        emit(
            self.session,
            ShipmentEvents.STATUS_CHANGED,
            shipment,
            previous_status=previous.value,
            reason=reason,
            actor_id=principal.user_id,
        )
        return await self.get(shipment_id, principal=principal)

    # --- packages --------------------------------------------------------------------

    async def add_package(
        self, shipment_id: uuid.UUID, payload: PackageCreate, *, principal: Principal
    ) -> Package:
        shipment = await self.get(shipment_id, principal=principal)
        self._assert_can_pack(shipment, principal)
        if shipment.status not in EDITABLE_STATES:
            raise ConflictError("Packages can only be added before dispatch.")
        package = Package(
            shipment_id=shipment.id,
            sequence_no=await self.repo.next_package_sequence(shipment.id),
            barcode=self._barcode(),
            **payload.model_dump(),
        )
        self.session.add(package)
        await self.session.flush()
        return package

    async def update_package(
        self, package_id: uuid.UUID, payload: PackageUpdate, *, principal: Principal
    ) -> Package:
        package = await self.session.get(Package, package_id)
        if package is None:
            raise NotFoundError("Package not found.")
        shipment = await self.get(package.shipment_id, principal=principal)
        self._assert_can_pack(shipment, principal)
        changes = payload.model_dump(exclude_unset=True)
        for field, value in changes.items():
            setattr(package, field, value)
        if changes.get("status") is PackageStatus.PACKED and package.packed_at is None:
            package.packed_at = datetime.now(UTC)
        await self.session.flush()
        return package

    # --- helpers ---------------------------------------------------------------------

    def _record(self, shipment: Shipment, previous, target, principal: Principal, reason) -> None:
        self.session.add(
            ShipmentStatusHistory(
                shipment_id=shipment.id,
                from_status=previous.value if previous else None,
                to_status=target.value,
                actor_id=principal.user_id,
                actor_role=principal.role.value,
                reason=reason,
            )
        )

    @staticmethod
    def _visible(shipment: Shipment, principal: Principal) -> bool:
        """Row scoping (ADR-009). Out of scope reads as "not found"."""
        if principal.sees_all_warehouses:
            return True
        if principal.role is UserRole.WAREHOUSE_OPERATOR:
            return principal.may_touch_warehouse(shipment.origin_warehouse_id)
        if principal.role is UserRole.COURIER:
            return principal.courier_id is not None and shipment.courier_id == principal.courier_id
        return False

    @staticmethod
    def _assert_can_pack(shipment: Shipment, principal: Principal) -> None:
        if principal.is_admin:
            return
        if principal.role is UserRole.WAREHOUSE_OPERATOR and principal.may_touch_warehouse(
            shipment.origin_warehouse_id
        ):
            return
        raise PermissionDeniedError("Only the originating warehouse may pack this shipment.")

    @staticmethod
    def _barcode() -> str:
        return f"{PACKAGE_BARCODE_PREFIX}{secrets.token_hex(8).upper()}"
