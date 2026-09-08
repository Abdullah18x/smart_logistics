"""Enumerations shared across models, schemas and services."""

from enum import IntEnum, StrEnum


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
    """Discriminator carried inside issued JWTs."""

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
    """ISO-8601 day numbering, Monday = 1."""

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
