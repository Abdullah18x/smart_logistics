"""Object factories for tests.

Every factory produces a valid, minimally-specified row and lets the test
override only the fields that matter to it. That keeps each test's setup down
to the one or two attributes it is actually about, so the assertion is not
buried in scaffolding.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessScope
from app.core.enums import (
    CourierAvailability,
    ReservationStatus,
    ServiceLevel,
    ShipmentPriority,
    ShipmentStatus,
    UserRole,
    UserStatus,
    VehicleType,
    WarehouseStatus,
    WarehouseType,
    Weekday,
    ZoneType,
)
from app.core.security import hash_password
from app.models.address import Address
from app.models.courier import Courier
from app.models.inventory_item import InventoryItem
from app.models.inventory_reservation import InventoryReservation
from app.models.package import Package
from app.models.shipment import Shipment
from app.models.shipment_item import ShipmentItem
from app.models.sku import Sku
from app.models.user import User
from app.models.user_warehouse_assignment import UserWarehouseAssignment
from app.models.warehouse import Warehouse
from app.models.warehouse_operating_hours import WarehouseOperatingHours
from app.models.warehouse_zone import WarehouseZone

#: The password every factory-made user is created with.
TEST_PASSWORD = "TestPassw0rd!2026"


def _suffix() -> str:
    return uuid.uuid4().hex[:10]


class Factory:
    """Builds persisted test data on one session."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- identity ----------------------------------------------------------

    async def user(
        self,
        role: UserRole = UserRole.ADMIN,
        *,
        email: str | None = None,
        password: str = TEST_PASSWORD,
        status: UserStatus = UserStatus.ACTIVE,
        full_name: str = "Test Person",
        **kwargs,
    ) -> User:
        user = User(
            email=email or f"{role.value}.{_suffix()}@test.transfleet.com",
            full_name=full_name,
            password_hash=hash_password(password),
            password_changed_at=datetime.now(UTC),
            role=role,
            status=status,
            **kwargs,
        )
        self.session.add(user)
        await self.session.flush()
        return user

    async def assign(self, user: User, warehouse: Warehouse) -> UserWarehouseAssignment:
        """Give a warehouse operator row-level access to a facility."""
        assignment = UserWarehouseAssignment(user_id=user.id, warehouse_id=warehouse.id)
        self.session.add(assignment)
        await self.session.flush()
        return assignment

    # --- warehouses --------------------------------------------------------

    async def warehouse(
        self,
        *,
        code: str | None = None,
        status: WarehouseStatus = WarehouseStatus.ACTIVE,
        type: WarehouseType = WarehouseType.FULFILLMENT_CENTER,
        city: str = "Karachi",
        capacity_units: int = 10_000,
        **kwargs,
    ) -> Warehouse:
        warehouse = Warehouse(
            # Codes are 16 chars max, so keep the random part short.
            code=(code or f"T{_suffix()[:7]}").upper(),
            name=kwargs.pop("name", "Test Facility"),
            type=type,
            status=status,
            address_line1="1 Test Road",
            city=city,
            country_code="PK",
            capacity_units=capacity_units,
            timezone=kwargs.pop("timezone", "Asia/Karachi"),
            **kwargs,
        )
        self.session.add(warehouse)
        await self.session.flush()
        return warehouse

    async def zone(
        self,
        warehouse: Warehouse,
        *,
        code: str | None = None,
        type: ZoneType = ZoneType.STORAGE,
        capacity_units: int = 500,
        **kwargs,
    ) -> WarehouseZone:
        zone = WarehouseZone(
            warehouse_id=warehouse.id,
            code=(code or f"Z{_suffix()[:6]}").upper(),
            name=kwargs.pop("name", "Test Zone"),
            type=type,
            capacity_units=capacity_units,
            **kwargs,
        )
        self.session.add(zone)
        await self.session.flush()
        return zone

    async def operating_hours(
        self,
        warehouse: Warehouse,
        *,
        day: Weekday = Weekday.MONDAY,
        opens_at: time | None = time(8, 0),
        closes_at: time | None = time(20, 0),
        is_closed: bool = False,
    ) -> WarehouseOperatingHours:
        row = WarehouseOperatingHours(
            warehouse_id=warehouse.id,
            day_of_week=day,
            opens_at=None if is_closed else opens_at,
            closes_at=None if is_closed else closes_at,
            is_closed=is_closed,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    # --- catalogue and stock ----------------------------------------------

    async def sku(
        self,
        *,
        code: str | None = None,
        weight_g: int = 500,
        is_active: bool = True,
        **kwargs,
    ) -> Sku:
        sku = Sku(
            code=code or f"SKU-{_suffix()}",
            name=kwargs.pop("name", "Test Product"),
            weight_g=weight_g,
            length_mm=kwargs.pop("length_mm", 100),
            width_mm=kwargs.pop("width_mm", 100),
            height_mm=kwargs.pop("height_mm", 100),
            is_active=is_active,
            **kwargs,
        )
        self.session.add(sku)
        await self.session.flush()
        return sku

    async def stock(
        self,
        warehouse: Warehouse,
        sku: Sku,
        *,
        on_hand_qty: int = 100,
        reserved_qty: int = 0,
        reorder_level: int = 0,
        **kwargs,
    ) -> InventoryItem:
        item = InventoryItem(
            warehouse_id=warehouse.id,
            sku_id=sku.id,
            on_hand_qty=on_hand_qty,
            reserved_qty=reserved_qty,
            reorder_level=reorder_level,
            **kwargs,
        )
        self.session.add(item)
        await self.session.flush()
        # available_qty is generated by Postgres; read it back.
        await self.session.refresh(item)
        return item

    async def reservation(
        self,
        item: InventoryItem,
        shipment_id: uuid.UUID,
        *,
        quantity: int = 1,
        status: ReservationStatus = ReservationStatus.HELD,
        expires_at: datetime | None = None,
    ) -> InventoryReservation:
        """A hold created directly, for tests about expiry and release."""
        reservation = InventoryReservation(
            inventory_item_id=item.id,
            shipment_id=shipment_id,
            warehouse_id=item.warehouse_id,
            sku_id=item.sku_id,
            quantity=quantity,
            status=status,
            expires_at=expires_at or datetime.now(UTC) + timedelta(hours=2),
            idempotency_key=f"test:{_suffix()}",
        )
        self.session.add(reservation)
        await self.session.flush()
        return reservation

    # --- couriers ----------------------------------------------------------

    async def courier(
        self,
        *,
        user: User | None = None,
        availability: CourierAvailability = CourierAvailability.AVAILABLE,
        home_city: str = "Karachi",
        **kwargs,
    ) -> Courier:
        courier = Courier(
            user_id=user.id if user else None,
            full_name=user.full_name if user else "Test Courier",
            phone=kwargs.pop("phone", "+923001234567"),
            employee_code=kwargs.pop("employee_code", f"CR-{_suffix()}"),
            vehicle_type=kwargs.pop("vehicle_type", VehicleType.VAN),
            capacity_kg=kwargs.pop("capacity_kg", 500),
            availability_status=availability,
            home_city=home_city,
            **kwargs,
        )
        self.session.add(courier)
        await self.session.flush()
        return courier

    # --- shipments ---------------------------------------------------------

    async def address(self, *, city: str = "Karachi", **kwargs) -> Address:
        address = Address(
            contact_name=kwargs.pop("contact_name", "Test Recipient"),
            contact_phone=kwargs.pop("contact_phone", "+923001234567"),
            line1=kwargs.pop("line1", "1 Delivery Street"),
            city=city,
            country_code=kwargs.pop("country_code", "PK"),
            **kwargs,
        )
        self.session.add(address)
        await self.session.flush()
        return address

    async def shipment(
        self,
        warehouse: Warehouse,
        *,
        status: ShipmentStatus = ShipmentStatus.CREATED,
        courier: Courier | None = None,
        created_by: User | None = None,
        service_level: ServiceLevel = ServiceLevel.STANDARD,
        priority: ShipmentPriority = ShipmentPriority.NORMAL,
        address: Address | None = None,
        **kwargs,
    ) -> Shipment:
        """A shipment created directly, bypassing the service.

        Used where the test is about something else — scoping, listing, state
        transitions — and going through ``ShipmentService.create`` would drag in
        stock reservation it does not care about.
        """
        destination = address or await self.address()
        shipment = Shipment(
            reference_no=kwargs.pop("reference_no", f"SL-TEST-{_suffix()}"),
            status=status,
            service_level=service_level,
            priority=priority,
            origin_warehouse_id=warehouse.id,
            courier_id=courier.id if courier else None,
            created_by=created_by.id if created_by else None,
            destination_address_id=destination.id,
            promised_delivery_at=kwargs.pop(
                "promised_delivery_at", datetime.now(UTC) + timedelta(days=3)
            ),
            **kwargs,
        )
        self.session.add(shipment)
        await self.session.flush()
        return shipment

    async def shipment_item(
        self, shipment: Shipment, sku: Sku, *, quantity: int = 1
    ) -> ShipmentItem:
        item = ShipmentItem(
            shipment_id=shipment.id,
            sku_id=sku.id,
            quantity=quantity,
            sku_code=sku.code,
            sku_name=sku.name,
            unit_weight_g=sku.weight_g,
            unit_value=sku.unit_value,
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def package(self, shipment: Shipment, *, sequence_no: int = 1, **kwargs) -> Package:
        package = Package(
            shipment_id=shipment.id,
            sequence_no=sequence_no,
            barcode=kwargs.pop("barcode", f"PKG{_suffix().upper()}"),
            weight_g=kwargs.pop("weight_g", 1000),
            **kwargs,
        )
        self.session.add(package)
        await self.session.flush()
        return package

    # --- helpers -----------------------------------------------------------

    @staticmethod
    def scope(
        user: User,
        *,
        warehouse_ids: list[uuid.UUID] | None = None,
        courier_id: uuid.UUID | None = None,
    ) -> AccessScope:
        """The access scope the dependency layer would have built for this user."""
        return AccessScope(
            role=user.role,
            user_id=user.id,
            warehouse_ids=warehouse_ids,
            courier_id=courier_id,
        )

    @staticmethod
    def auth_headers(user: User) -> dict[str, str]:
        """A bearer header for this user, as ``POST /auth/login`` would issue."""
        from app.core.tokens import create_access_token

        token, _ = create_access_token(user.id, user.role)
        return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def factory(db_session: AsyncSession) -> Factory:
    return Factory(db_session)
