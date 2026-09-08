"""System-defined statuses, roles and categories.

Every enum below is a closed vocabulary the application enforces. Most of them
back a real Postgres enum type (see ``pg_enum()`` in ``models.base``), which
means the database already rejects a value outside this list — but a database
constraint is not documentation. Without this file, the only way to learn
what statuses a shipment can hold is to read a migration or run ``\\dT`` in
``psql``. This is that reference, kept in the language the rest of the
application is written in, so an editor's "find usages" and "go to
definition" work on it the same as anything else.

``DB_BACKED_ENUMS`` at the bottom names which of these correspond to a real
Postgres type, and to which one. ``tests/integration/test_enum_constants.py``
reads that registry and queries the live database, so a Python enum edited
without a matching migration — or a migration that touches a type without the
Python enum being updated — fails the suite instead of surfacing as a
mismatched dropdown two weeks later.
"""

from dataclasses import dataclass
from enum import IntEnum, StrEnum

from app.constants.database import (
    COURIER_SCHEMA,
    IDENTITY_SCHEMA,
    INVENTORY_SCHEMA,
    SHIPMENT_SCHEMA,
    WAREHOUSE_SCHEMA,
)


class UserRole(StrEnum):
    """Roles defined by the SmartLogistics access model."""

    ADMIN = "admin"
    CUSTOMER_SUPPORT = "customer_support"
    WAREHOUSE_OPERATOR = "warehouse_operator"
    COURIER = "courier"


class UserStatus(StrEnum):
    """Account lifecycle state, independent of authentication outcome."""

    PENDING_ACTIVATION = "pending_activation"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DEACTIVATED = "deactivated"


class TokenType(StrEnum):
    """Discriminator carried inside issued JWTs.

    Not backed by a Postgres enum — a JWT payload is never stored as a
    database column, so there is nothing here for a migration to constrain.
    """

    ACCESS = "access"
    REFRESH = "refresh"


class WarehouseType(StrEnum):
    """What role a facility plays in the network."""

    FULFILLMENT_CENTER = "fulfillment_center"
    DISTRIBUTION_HUB = "distribution_hub"
    REGIONAL_DEPOT = "regional_depot"
    RETURNS_CENTER = "returns_center"


class WarehouseStatus(StrEnum):
    """Operational state. Only ACTIVE facilities accept dispatch."""

    ACTIVE = "active"
    MAINTENANCE = "maintenance"
    INACTIVE = "inactive"


class ZoneType(StrEnum):
    """Functional area within a warehouse, following goods flow."""

    RECEIVING = "receiving"
    STORAGE = "storage"
    PICKING = "picking"
    PACKING = "packing"
    STAGING = "staging"
    DISPATCH = "dispatch"
    RETURNS = "returns"
    QUARANTINE = "quarantine"


class Weekday(IntEnum):
    """ISO-8601 day numbering, Monday = 1.

    Not backed by a Postgres enum — ``warehouse_operating_hours.day_of_week``
    is a plain ``SmallInteger``, since the value is a number the database can
    range-check (1 to 7) rather than a fixed vocabulary of labels.
    """

    MONDAY = 1
    TUESDAY = 2
    WEDNESDAY = 3
    THURSDAY = 4
    FRIDAY = 5
    SATURDAY = 6
    SUNDAY = 7


class ShipmentStatus(StrEnum):
    """Lifecycle of a shipment. Transitions are declared once, in
    ``core.shipment_state_machine``."""

    CREATED = "created"
    READY_FOR_DISPATCH = "ready_for_dispatch"
    DISPATCHING = "dispatching"
    DISPATCH_FAILED = "dispatch_failed"
    DISPATCHED = "dispatched"
    PICKED_UP = "picked_up"
    IN_TRANSIT = "in_transit"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"
    DELIVERY_FAILED = "delivery_failed"
    RETURN_INITIATED = "return_initiated"
    RETURNED = "returned"
    CANCELLED = "cancelled"


class ServiceLevel(StrEnum):
    """Commercial promise, which drives the delivery deadline."""

    ECONOMY = "economy"
    STANDARD = "standard"
    EXPRESS = "express"
    SAME_DAY = "same_day"


class ShipmentPriority(StrEnum):
    """Operational override, independent of what the customer paid for.

    A standard shipment already late may be raised to CRITICAL; this is what
    dispatch prioritisation sorts on.
    """

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class ReservationStatus(StrEnum):
    """Lifecycle of an inventory hold."""

    HELD = "held"
    COMMITTED = "committed"
    RELEASED = "released"


class StockMovementType(StrEnum):
    """Why stock changed. Every movement is an immutable ledger entry."""

    INBOUND_RECEIPT = "inbound_receipt"
    OUTBOUND_DISPATCH = "outbound_dispatch"
    RETURN_RECEIPT = "return_receipt"
    ADJUSTMENT = "adjustment"
    DAMAGE_WRITE_OFF = "damage_write_off"
    TRANSFER_IN = "transfer_in"
    TRANSFER_OUT = "transfer_out"


class PackageStatus(StrEnum):
    """Packing progress for one parcel."""

    PENDING = "pending"
    PACKED = "packed"
    LABELLED = "labelled"
    DISPATCHED = "dispatched"


class VehicleType(StrEnum):
    """Determines capacity, and which shipments a courier can physically take."""

    MOTORCYCLE = "motorcycle"
    CAR = "car"
    VAN = "van"
    TRUCK = "truck"
    BICYCLE = "bicycle"


class CourierAvailability(StrEnum):
    """Whether a courier can be offered new work right now."""

    AVAILABLE = "available"
    ON_DUTY = "on_duty"
    ON_BREAK = "on_break"
    OFF_DUTY = "off_duty"


class AssignmentStatus(StrEnum):
    """Lifecycle of one courier assignment."""

    OFFERED = "offered"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    REASSIGNED = "reassigned"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class DbBackedEnum:
    """Where one Python enum's Postgres counterpart lives.

    ``pg_name`` and ``schema`` are exactly the arguments passed to
    ``pg_enum()`` when the column was declared — this is what lets the check
    in ``test_enum_constants.py`` find the matching Postgres type.
    """

    enum_class: type[StrEnum]
    pg_name: str
    schema: str


#: Every enum with a real Postgres type behind it. Add an entry here whenever
#: a new ``pg_enum()`` column is declared — the drift test iterates this list,
#: so an enum left out of it is an enum nothing verifies against the database.
DB_BACKED_ENUMS: tuple[DbBackedEnum, ...] = (
    DbBackedEnum(UserRole, "user_role", IDENTITY_SCHEMA),
    DbBackedEnum(UserStatus, "user_status", IDENTITY_SCHEMA),
    DbBackedEnum(WarehouseType, "warehouse_type", WAREHOUSE_SCHEMA),
    DbBackedEnum(WarehouseStatus, "warehouse_status", WAREHOUSE_SCHEMA),
    DbBackedEnum(ZoneType, "zone_type", WAREHOUSE_SCHEMA),
    DbBackedEnum(ShipmentStatus, "shipment_status", SHIPMENT_SCHEMA),
    DbBackedEnum(ServiceLevel, "service_level", SHIPMENT_SCHEMA),
    DbBackedEnum(ShipmentPriority, "shipment_priority", SHIPMENT_SCHEMA),
    DbBackedEnum(ReservationStatus, "reservation_status", INVENTORY_SCHEMA),
    DbBackedEnum(StockMovementType, "stock_movement_type", INVENTORY_SCHEMA),
    DbBackedEnum(PackageStatus, "package_status", SHIPMENT_SCHEMA),
    DbBackedEnum(VehicleType, "vehicle_type", COURIER_SCHEMA),
    DbBackedEnum(CourierAvailability, "courier_availability", COURIER_SCHEMA),
    DbBackedEnum(AssignmentStatus, "assignment_status", COURIER_SCHEMA),
)
