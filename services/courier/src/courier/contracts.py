"""Contracts for the events Courier publishes on ``courier.events``."""

from courier.models import (
    AssignmentMethod,
    AssignmentStatus,
    Availability,
    CourierStatus,
    VehicleType,
)
from sl_platform.contracts import (
    array,
    boolean,
    enum,
    event,
    extend,
    integer,
    nullable,
    register,
    string,
    timestamp,
    uuid,
)

PROFILE = event(
    "courier",
    {
        "id": uuid(),
        "code": string(),
        "user_id": nullable(uuid()),
        "full_name": string(),
        "phone": string(),
        "email": nullable(string()),
        "vehicle_type": enum(*(v.value for v in VehicleType)),
        "home_city": string(),
        "home_warehouse_id": nullable(uuid()),
        "status": enum(*(s.value for s in CourierStatus)),
        "availability": enum(*(a.value for a in Availability)),
        "max_active_shipments": integer(minimum=1),
        "active_assignments": integer(minimum=0),
        "deleted": boolean(),
        "updated_at": timestamp(),
    },
    required=("id", "code", "full_name", "status", "availability", "deleted", "updated_at"),
    description="The courier's profile after the change.",
)

ASSIGNMENT = event(
    "courier assignment",
    {
        "assignment_id": uuid(),
        "shipment_id": uuid(),
        "courier_id": uuid(),
        "warehouse_id": uuid(),
        "status": enum(*(s.value for s in AssignmentStatus)),
        "method": enum(*(m.value for m in AssignmentMethod)),
        "assigned_at": timestamp(),
        "ended_at": nullable(timestamp()),
        "end_reason": nullable(string()),
    },
    required=("assignment_id", "shipment_id", "courier_id", "status", "assigned_at"),
    description="A shipment handed to (or taken from) a courier.",
)

CONTRACTS = {
    "courier.created": extend(PROFILE, "courier.created"),
    "courier.updated": extend(PROFILE, "courier.updated", changed=array(string())),
    "courier.status_changed": extend(PROFILE, "courier.status_changed", reason=nullable(string())),
    "courier.availability_changed": extend(PROFILE, "courier.availability_changed"),
    "courier.deleted": extend(PROFILE, "courier.deleted"),
    "courier.restored": extend(PROFILE, "courier.restored"),
    "courier.assigned": extend(ASSIGNMENT, "courier.assigned"),
    "courier.unassigned": extend(ASSIGNMENT, "courier.unassigned"),
}

# Known to this process from import: add_event checks every payload against them.
register(CONTRACTS)
