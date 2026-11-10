"""Contracts for the events Tracking publishes on ``tracking.events``."""

from sl_platform.contracts import (
    enum,
    event,
    nullable,
    number,
    obj,
    register,
    string,
    timestamp,
    uuid,
)
from tracking.milestones import Milestone

LOCATION = obj(
    {"latitude": number(), "longitude": number(), "accuracy_m": nullable(number())},
    required=("latitude", "longitude"),
)
FIELDS = {
    "shipment_id": uuid(),
    "milestone": nullable(enum(*(m.value for m in Milestone))),
    "occurred_at": timestamp(),
    "courier_id": nullable(uuid()),
    "location": nullable(LOCATION),
    "failure_reason": nullable(string()),
    "source": enum("courier", "dispatch", "shipment"),
}

CONTRACTS = {
    "tracking.milestone_recorded": event(
        "tracking.milestone_recorded",
        FIELDS,
        required=("shipment_id", "milestone", "occurred_at", "source"),
        description="A milestone on a shipment's journey, from the field or the system.",
    ),
    "tracking.location_recorded": event(
        "tracking.location_recorded",
        FIELDS,
        required=("shipment_id", "occurred_at", "location", "source"),
        description="A courier's position fix.",
    ),
}

# Known to this process from import: add_event checks every payload against them.
register(CONTRACTS)
