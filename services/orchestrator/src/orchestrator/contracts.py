"""Workflow and activity payloads. Plain dataclasses of strings and numbers,
so they serialise the same way in every worker and survive code changes."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DispatchInput:
    shipment_id: str
    attempt: int
    warehouse_id: str
    courier_wait_attempts: int = 5
    courier_wait_seconds: float = 60.0
    pickup_timeout_seconds: float = 4 * 3600.0
    max_delivery_attempts: int = 3


@dataclass
class ShipmentFacts:
    shipment_id: str
    reference_no: str
    warehouse_id: str
    total_weight_g: int
    package_count: int
    destination_city: str
    items: list[tuple[str, int]] = field(default_factory=list)


@dataclass
class AssignCourierInput:
    shipment_id: str
    warehouse_id: str
    total_weight_g: int


@dataclass
class CourierAssignment:
    assignment_id: str
    courier_id: str
    courier_code: str


@dataclass
class TrackingInput:
    shipment_id: str
    reference_no: str
    courier_id: str
    warehouse_id: str
    destination_city: str


@dataclass
class MarkDispatchedInput:
    shipment_id: str
    courier_id: str


@dataclass
class EndInput:
    shipment_id: str
    reason: str


@dataclass
class DispatchResult:
    shipment_id: str
    status: str  # "dispatched" | "dispatch_failed" | "skipped"
    courier_id: str | None = None
    reason: str | None = None
    steps: list[str] = field(default_factory=list)


@dataclass
class DeliveryInput:
    shipment_id: str
    courier_id: str
    pickup_timeout_seconds: float = 4 * 3600.0
    max_attempts: int = 3


@dataclass
class FieldUpdate:
    """A courier's milestone, forwarded from the tracking service."""

    event_id: str
    milestone: str
    occurred_at: str
    failure_reason: str | None = None


@dataclass
class ApplyStatusInput:
    shipment_id: str
    status: str
    reason: str | None = None


@dataclass
class ReassignInput:
    shipment_id: str
    reason: str


@dataclass
class DeliveryResult:
    shipment_id: str
    status: str
    delivery_attempts: int
    escalations: int
    applied: list[str] = field(default_factory=list)
