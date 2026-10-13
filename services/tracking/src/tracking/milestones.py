"""The journey's milestone vocabulary and its order.

Tracking records what couriers report; it does not decide shipment status
(the delivery workflow does). It only needs the *order*, to know which
milestone is the latest when updates arrive late or out of order.
"""

from enum import StrEnum


class Milestone(StrEnum):
    DISPATCHED = "dispatched"
    PICKED_UP = "picked_up"
    IN_TRANSIT = "in_transit"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERY_FAILED = "delivery_failed"
    DELIVERED = "delivered"
    RETURN_INITIATED = "return_initiated"
    RETURNED = "returned"


#: What a courier may report from the field.
COURIER_MILESTONES = frozenset(
    {
        Milestone.PICKED_UP,
        Milestone.IN_TRANSIT,
        Milestone.OUT_FOR_DELIVERY,
        Milestone.DELIVERY_FAILED,
        Milestone.DELIVERED,
    }
)

TERMINAL = frozenset({Milestone.DELIVERED, Milestone.RETURNED})


class JourneyStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class UpdateType(StrEnum):
    MILESTONE = "milestone"
    LOCATION = "location"
