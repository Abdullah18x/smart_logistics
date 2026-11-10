"""Contracts for the events Warehouse publishes on ``warehouse.events``.

Every event is a full snapshot (event-carried state transfer): consumers keep a
local replica and keep the newest by ``updated_at``.
"""

from sl_platform.contracts import (
    boolean,
    enum,
    event,
    extend,
    integer,
    nullable,
    number,
    register,
    string,
    timestamp,
    uuid,
)
from warehouse.models import WarehouseStatus, WarehouseType

STATUSES = enum(*(s.value for s in WarehouseStatus))

SNAPSHOT = event(
    "warehouse",
    {
        "id": uuid(),
        "code": string(),
        "name": string(),
        "type": enum(*(t.value for t in WarehouseType)),
        "status": STATUSES,
        "city": string(),
        "region": nullable(string()),
        "country_code": string(),
        "timezone": string(),
        "latitude": nullable(number()),
        "longitude": nullable(number()),
        "entrance_latitude": nullable(number()),
        "entrance_longitude": nullable(number()),
        "geofence_radius_m": nullable(integer()),
        "deleted": boolean(),
        "updated_at": timestamp(),
    },
    required=("id", "code", "name", "status", "city", "deleted", "updated_at"),
    description="The warehouse's full state after the change.",
)

CONTRACTS = {
    "warehouse.created": extend(SNAPSHOT, "warehouse.created"),
    "warehouse.updated": extend(SNAPSHOT, "warehouse.updated"),
    "warehouse.status_changed": extend(
        SNAPSHOT,
        "warehouse.status_changed",
        required=("previous_status",),
        previous_status=STATUSES,
        reason=nullable(string()),
    ),
    "warehouse.deleted": extend(SNAPSHOT, "warehouse.deleted"),
    "warehouse.restored": extend(SNAPSHOT, "warehouse.restored"),
}

# Known to this process from import: add_event checks every payload against them.
register(CONTRACTS)
