"""Contracts for the events Shipment publishes on ``shipment.events``."""

from shipment.models import ServiceLevel, ShipmentPriority, ShipmentStatus
from sl_platform.contracts import (
    array,
    enum,
    event,
    extend,
    integer,
    nullable,
    obj,
    register,
    string,
    timestamp,
    uuid,
)

LINE = obj({"sku_id": uuid(), "quantity": integer(minimum=1)}, required=("sku_id", "quantity"))

STATUSES = enum(*(s.value for s in ShipmentStatus))

SUMMARY = event(
    "shipment",
    {
        "id": uuid(),
        "reference_no": string(),
        "customer_reference": nullable(string()),
        "status": STATUSES,
        "service_level": enum(*(s.value for s in ServiceLevel)),
        "priority": enum(*(p.value for p in ShipmentPriority)),
        "origin_warehouse_id": uuid(),
        "origin_warehouse_code": nullable(string()),
        "courier_id": nullable(uuid()),
        "total_weight_g": nullable(integer(minimum=0)),
        "promised_delivery_at": nullable(timestamp()),
        "version": integer(minimum=1),
    },
    required=("id", "reference_no", "status", "origin_warehouse_id", "version"),
    description="The shipment's summary after the change.",
)

CONTRACTS = {
    "shipment.created": extend(
        SUMMARY,
        "shipment.created",
        required=("items",),
        created_by=nullable(uuid()),
        destination_city=string(),
        recipient=obj(
            {"name": string(), "phone": string(), "email": nullable(string())},
            required=("name", "phone"),
        ),
        items=array(
            obj(
                {"sku_id": uuid(), "quantity": integer(minimum=1)}, required=("sku_id", "quantity")
            ),
            minItems=1,
        ),
    ),
    "shipment.updated": extend(SUMMARY, "shipment.updated", changed=array(string())),
    "shipment.status_changed": extend(
        SUMMARY,
        "shipment.status_changed",
        required=("previous_status",),
        previous_status=STATUSES,
        reason=nullable(string()),
        actor_id=nullable(uuid()),
    ),
    "shipment.dispatch_requested": extend(
        SUMMARY,
        "shipment.dispatch_requested",
        required=("workflow_id", "attempt"),
        workflow_id=string(),
        attempt=integer(minimum=1),
        requested_by=nullable(uuid()),
    ),
}

# Known to this process from import: add_event checks every payload against them.
register(CONTRACTS)
