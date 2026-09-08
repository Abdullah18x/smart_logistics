"""Shipment lifecycle business logic."""

# The service defines a method named `list`, which shadows the builtin inside
# the class body. Postponing annotation evaluation keeps `list[...]` hints valid.
from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessScope
from app.core.enums import (
    PackageStatus,
    ServiceLevel,
    ShipmentStatus,
    UserRole,
    WarehouseStatus,
)
from app.core.exceptions import ConflictError, NotFoundError, PermissionDeniedError
from app.core.shipment_state_machine import assert_transition
from app.models.address import Address
from app.models.package import Package
from app.models.shipment import Shipment
from app.models.shipment_item import ShipmentItem
from app.models.sku import Sku
from app.models.warehouse import Warehouse
from app.repositories.shipment_repository import ShipmentRepository
from app.repositories.warehouse_repository import WarehouseRepository
from app.schemas.package import PackageCreate, PackageUpdate
from app.schemas.shipment import ShipmentCreate, ShipmentUpdate
from app.services.inventory_service import InventoryService

#: How long each service level promises, from creation.
SERVICE_LEVEL_WINDOWS: dict[ServiceLevel, timedelta] = {
    ServiceLevel.SAME_DAY: timedelta(hours=8),
    ServiceLevel.EXPRESS: timedelta(days=1),
    ServiceLevel.STANDARD: timedelta(days=3),
    ServiceLevel.ECONOMY: timedelta(days=5),
}

#: Which role may drive each transition. The dispatch and delivery workflows
#: take over the middle of this table in Phase 2; until then an admin can drive
#: them manually so the lifecycle is demonstrable end to end.
TRANSITION_ROLES: dict[ShipmentStatus, frozenset[UserRole]] = {
    ShipmentStatus.READY_FOR_DISPATCH: frozenset({UserRole.ADMIN, UserRole.WAREHOUSE_OPERATOR}),
    ShipmentStatus.DISPATCHING: frozenset({UserRole.ADMIN}),
    ShipmentStatus.DISPATCHED: frozenset({UserRole.ADMIN}),
    ShipmentStatus.DISPATCH_FAILED: frozenset({UserRole.ADMIN}),
    ShipmentStatus.PICKED_UP: frozenset({UserRole.ADMIN, UserRole.COURIER}),
    ShipmentStatus.IN_TRANSIT: frozenset({UserRole.ADMIN, UserRole.COURIER}),
    ShipmentStatus.OUT_FOR_DELIVERY: frozenset({UserRole.ADMIN, UserRole.COURIER}),
    ShipmentStatus.DELIVERED: frozenset({UserRole.ADMIN, UserRole.COURIER}),
    ShipmentStatus.DELIVERY_FAILED: frozenset({UserRole.ADMIN, UserRole.COURIER}),
    ShipmentStatus.RETURN_INITIATED: frozenset({UserRole.ADMIN, UserRole.CUSTOMER_SUPPORT}),
    ShipmentStatus.RETURNED: frozenset({UserRole.ADMIN, UserRole.WAREHOUSE_OPERATOR}),
    ShipmentStatus.CANCELLED: frozenset({UserRole.ADMIN, UserRole.CUSTOMER_SUPPORT}),
}

#: Statuses in which stock is held for the shipment and must be given back if
#: it is cancelled. Mirrors HOLDS_INVENTORY in the state machine.
RELEASES_STOCK_ON_EXIT = frozenset(
    {
        ShipmentStatus.CREATED,
        ShipmentStatus.READY_FOR_DISPATCH,
        ShipmentStatus.DISPATCHING,
        ShipmentStatus.DISPATCH_FAILED,
    }
)

#: Editing content after dispatch would misrepresent what was actually sent.
EDITABLE_STATUSES = frozenset(
    {ShipmentStatus.CREATED, ShipmentStatus.READY_FOR_DISPATCH, ShipmentStatus.DISPATCH_FAILED}
)


class ShipmentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.shipments = ShipmentRepository(session)
        self.warehouses = WarehouseRepository(session)
        self.inventory = InventoryService(session)

    # --- reads -------------------------------------------------------------

    async def get(self, shipment_id: uuid.UUID, *, scope: AccessScope) -> Shipment:
        shipment = await self.shipments.get_with_detail(shipment_id)
        if shipment is None:
            raise NotFoundError("Shipment not found.")
        self._assert_visible(shipment, scope=scope)
        return shipment

    async def list(self, *, scope: AccessScope, **filters) -> tuple[list[Shipment], int]:
        return await self.shipments.list_shipments(
            allowed_warehouse_ids=(
                scope.warehouse_ids if scope.role is UserRole.WAREHOUSE_OPERATOR else None
            ),
            restrict_to_courier_id=(scope.courier_id if scope.role is UserRole.COURIER else None),
            **filters,
        )

    # --- writes ------------------------------------------------------------

    async def create(self, payload: ShipmentCreate, *, scope: AccessScope) -> Shipment:
        warehouse = await self.session.get(Warehouse, payload.origin_warehouse_id)
        if warehouse is None:
            raise NotFoundError("Origin warehouse not found.")
        if warehouse.status is not WarehouseStatus.ACTIVE:
            raise ConflictError(
                f"Warehouse {warehouse.code} is {warehouse.status.value} and cannot originate "
                "shipments."
            )

        skus = await self._load_skus([item.sku_id for item in payload.items])

        address = Address(**payload.destination_address.model_dump())
        self.session.add(address)
        await self.session.flush()

        shipment = Shipment(
            reference_no=await self.shipments.next_reference_no(),
            customer_reference=payload.customer_reference,
            status=ShipmentStatus.CREATED,
            service_level=payload.service_level,
            priority=payload.priority,
            origin_warehouse_id=payload.origin_warehouse_id,
            created_by=scope.user_id,
            destination_address_id=address.id,
            declared_value=payload.declared_value,
            currency=payload.currency,
            special_instructions=payload.special_instructions,
            promised_delivery_at=(
                payload.promised_delivery_at
                or datetime.now(UTC) + SERVICE_LEVEL_WINDOWS[payload.service_level]
            ),
        )
        self.session.add(shipment)
        await self.session.flush()

        total_weight = 0
        for line in payload.items:
            sku = skus[line.sku_id]
            # Snapshot the SKU: a later correction to the product must not
            # rewrite what this consignment actually contained.
            self.session.add(
                ShipmentItem(
                    shipment_id=shipment.id,
                    sku_id=sku.id,
                    quantity=line.quantity,
                    sku_code=sku.code,
                    sku_name=sku.name,
                    unit_weight_g=sku.weight_g,
                    unit_value=sku.unit_value,
                )
            )
            total_weight += sku.weight_g * line.quantity

        for index, parcel in enumerate(payload.packages, start=1):
            self.session.add(
                Package(
                    shipment_id=shipment.id,
                    sequence_no=index,
                    barcode=self._barcode(),
                    weight_g=parcel.weight_g,
                    length_mm=parcel.length_mm,
                    width_mm=parcel.width_mm,
                    height_mm=parcel.height_mm,
                )
            )

        shipment.total_weight_g = total_weight

        # Hold the stock now, in the same transaction that creates the shipment.
        # Two requests for the same units serialise on the inventory row lock,
        # so the first to arrive wins and the second is refused rather than
        # both being accepted and one failing later at dispatch.
        await self.inventory.reserve_for_shipment(
            shipment_id=shipment.id,
            warehouse_id=payload.origin_warehouse_id,
            lines=[
                (skus[line.sku_id].id, line.quantity, skus[line.sku_id].code)
                for line in payload.items
            ],
        )

        await self.session.commit()
        return await self.get(shipment.id, scope=scope)

    async def update(
        self, shipment_id: uuid.UUID, payload: ShipmentUpdate, *, scope: AccessScope
    ) -> Shipment:
        shipment = await self.get(shipment_id, scope=scope)
        if shipment.status not in EDITABLE_STATUSES:
            raise ConflictError(f"A shipment in {shipment.status.value} can no longer be edited.")
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(shipment, field, value)
        await self.session.commit()
        return await self.get(shipment_id, scope=scope)

    async def change_status(
        self,
        shipment_id: uuid.UUID,
        target: ShipmentStatus,
        *,
        scope: AccessScope,
        reason: str | None = None,
    ) -> Shipment:
        shipment = await self.get(shipment_id, scope=scope)

        allowed_roles = TRANSITION_ROLES.get(target, frozenset())
        if scope.role not in allowed_roles:
            raise PermissionDeniedError(f"Your role cannot move a shipment to {target.value}.")
        if scope.role is UserRole.WAREHOUSE_OPERATOR and not scope.may_touch_warehouse(
            shipment.origin_warehouse_id
        ):
            raise PermissionDeniedError("This shipment is not from one of your warehouses.")

        # The single source of truth for what may follow what.
        assert_transition(shipment.status, target)

        now = datetime.now(UTC)
        previous = shipment.status
        shipment.status = target
        shipment.version += 1

        # Stock effects follow the transition, inside the same transaction.
        if target is ShipmentStatus.DISPATCHED:
            # The goods have physically left: holds become real reductions.
            await self.inventory.commit_for_shipment(shipment.id)
        elif target is ShipmentStatus.CANCELLED and previous in RELEASES_STOCK_ON_EXIT:
            await self.inventory.release_for_shipment(shipment.id, reason or "Shipment cancelled")

        # Each timestamp is written once, by the transition that earns it.
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

        await self.session.commit()
        return await self.get(shipment_id, scope=scope)

    async def cancel(self, shipment_id: uuid.UUID, reason: str, *, scope: AccessScope) -> Shipment:
        return await self.change_status(
            shipment_id, ShipmentStatus.CANCELLED, scope=scope, reason=reason
        )

    # --- packages ----------------------------------------------------------

    async def add_package(
        self, shipment_id: uuid.UUID, payload: PackageCreate, *, scope: AccessScope
    ) -> Package:
        shipment = await self.get(shipment_id, scope=scope)
        self._assert_can_pack(shipment, scope=scope)
        if shipment.status not in EDITABLE_STATUSES:
            raise ConflictError("Packages can only be added before dispatch.")

        package = Package(
            shipment_id=shipment.id,
            sequence_no=await self.shipments.next_package_sequence(shipment.id),
            barcode=self._barcode(),
            weight_g=payload.weight_g,
            length_mm=payload.length_mm,
            width_mm=payload.width_mm,
            height_mm=payload.height_mm,
        )
        self.session.add(package)
        await self.session.commit()
        return package

    async def update_package(
        self, package_id: uuid.UUID, payload: PackageUpdate, *, scope: AccessScope
    ) -> Package:
        package = await self.session.get(Package, package_id)
        if package is None:
            raise NotFoundError("Package not found.")
        shipment = await self.get(package.shipment_id, scope=scope)
        self._assert_can_pack(shipment, scope=scope)

        changes = payload.model_dump(exclude_unset=True)
        for field, value in changes.items():
            setattr(package, field, value)
        if changes.get("status") is PackageStatus.PACKED and package.packed_at is None:
            package.packed_at = datetime.now(UTC)
        await self.session.commit()
        return package

    # --- helpers -----------------------------------------------------------

    def _assert_visible(self, shipment: Shipment, *, scope: AccessScope) -> None:
        """Row-level read scoping (ADR-009)."""
        if scope.role in (UserRole.ADMIN, UserRole.CUSTOMER_SUPPORT):
            return
        if scope.role is UserRole.WAREHOUSE_OPERATOR:
            if scope.may_touch_warehouse(shipment.origin_warehouse_id):
                return
        elif scope.role is UserRole.COURIER and shipment.courier_id == scope.courier_id:
            return
        # "Not found" rather than "forbidden": the existence of another
        # courier's shipment is not information this caller should gain.
        raise NotFoundError("Shipment not found.")

    def _assert_can_pack(self, shipment: Shipment, *, scope: AccessScope) -> None:
        if scope.is_admin:
            return
        if scope.role is UserRole.WAREHOUSE_OPERATOR and scope.may_touch_warehouse(
            shipment.origin_warehouse_id
        ):
            return
        raise PermissionDeniedError("Only the originating warehouse may pack this shipment.")

    async def _load_skus(self, sku_ids: list[uuid.UUID]) -> dict[uuid.UUID, Sku]:
        rows = await self.session.scalars(select(Sku).where(Sku.id.in_(sku_ids)))
        skus = {sku.id: sku for sku in rows.all()}
        missing = set(sku_ids) - skus.keys()
        if missing:
            raise NotFoundError(f"Unknown SKU(s): {', '.join(str(m) for m in sorted(missing))}")
        inactive = [s.code for s in skus.values() if not s.is_active]
        if inactive:
            raise ConflictError(f"Inactive SKU(s): {', '.join(sorted(inactive))}")
        return skus

    @staticmethod
    def _barcode() -> str:
        return f"PKG{secrets.token_hex(8).upper()}"
