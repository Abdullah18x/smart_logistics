"""Who is told what, and when: the notification flows, as one table.

Each rule maps an event (and, for status changes, the new status) to a
template, an audience and the channels to use. ``plan`` turns one event into
the messages to create; the service then renders, stores and queues them.

Shipment status changes are the source of truth for customers — not tracking
milestones — because the delivery workflow has already deduplicated and
ordered them: a customer never gets "out for delivery" twice, or after
"delivered".
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from notification.models import Channel


class Audience(StrEnum):
    CUSTOMER = "customer"
    COURIER = "courier"
    OPERATIONS = "operations"


@dataclass(frozen=True)
class Rule:
    template: str
    audience: Audience
    channels: tuple[Channel, ...]


E, S, P = Channel.EMAIL, Channel.SMS, Channel.PUSH

#: shipment.status_changed -> new status -> rule
ON_STATUS: dict[str, Rule] = {
    "dispatched": Rule("shipment_dispatched", Audience.CUSTOMER, (E, S)),
    "out_for_delivery": Rule("out_for_delivery", Audience.CUSTOMER, (S,)),
    "delivered": Rule("delivered", Audience.CUSTOMER, (E, S)),
    "delivery_failed": Rule("delivery_attempt_failed", Audience.CUSTOMER, (S,)),
    "return_initiated": Rule("return_started", Audience.CUSTOMER, (E,)),
    "cancelled": Rule("shipment_cancelled", Audience.CUSTOMER, (E,)),
    "dispatch_failed": Rule("dispatch_failed", Audience.OPERATIONS, (E,)),
}

#: other events -> rule
ON_EVENT: dict[str, Rule] = {
    "shipment.created": Rule("shipment_confirmed", Audience.CUSTOMER, (E,)),
    "courier.assigned": Rule("new_assignment", Audience.COURIER, (P, S)),
}


def rule_for(event_type: str, payload: dict) -> Rule | None:
    if event_type == "shipment.status_changed":
        return ON_STATUS.get(payload.get("status", ""))
    return ON_EVENT.get(event_type)


@dataclass(frozen=True)
class Contact:
    """Where one person is reachable."""

    name: str | None
    email: str | None = None
    phone: str | None = None
    #: For push: the courier the app is signed in as.
    device: str | None = None

    def address(self, channel: Channel) -> str | None:
        return {Channel.EMAIL: self.email, Channel.SMS: self.phone, Channel.PUSH: self.device}[
            channel
        ]
