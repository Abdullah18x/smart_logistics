from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from analytics.models import IssueKind


class Period(BaseModel):
    start: date = Field(alias="from")
    end: date = Field(alias="to")

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


class CourierUtilisation(BaseModel):
    active_couriers: int
    busy_couriers: int = Field(description="Active couriers carrying at least one shipment.")
    active_assignments: int
    capacity: int = Field(description="Sum of the active couriers' max_active_shipments.")
    utilisation: float | None = Field(description="active_assignments / capacity, right now.")


class FailedEvents(BaseModel):
    dead_letters: int = Field(description="Events a consumer gave up on (dlq.* topics).")
    dispatch_failures: int = Field(description="Dispatch workflows that ended in failure.")


class Summary(BaseModel):
    """The nine metrics for a period."""

    period: Period
    total_shipments: int = Field(description="Shipments created in the period.")
    dispatched: int
    delivered: int
    failed_deliveries: int = Field(description="Failed delivery attempts.")
    failed_delivery_rate: float | None
    average_delivery_hours: float | None = Field(
        description="Mean time from creation to delivery, for shipments delivered in the period."
    )
    returns: int
    return_rate: float | None = Field(description="Returns started per shipment dispatched.")
    cancelled: int
    courier_utilisation: CourierUtilisation
    warehouse_throughput: list[WarehouseThroughput]
    failed_events: FailedEvents | None = Field(
        description="Pipeline-wide; only for admin and customer support."
    )


class WarehouseThroughput(BaseModel):
    warehouse_id: uuid.UUID
    warehouse_code: str | None
    created: int
    dispatched: int
    delivered: int
    failed_deliveries: int
    returns: int
    dispatch_failures: int
    average_delivery_hours: float | None
    dispatched_per_day: float


class CourierStats(BaseModel):
    courier_id: uuid.UUID
    code: str | None
    full_name: str | None
    status: str | None
    assigned: int
    delivered: int
    failed_deliveries: int
    active_assignments: int | None
    max_active_shipments: int | None
    utilisation: float | None = Field(description="Current load / capacity.")


class Point(BaseModel):
    day: date
    value: int


class Series(BaseModel):
    metric: str
    period: Period
    points: list[Point]
    total: int


class IssueRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: IssueKind
    source: str
    occurred_at: datetime
    reason: str
    detail: str | None
    event_id: str | None
    event_type: str | None
    shipment_id: uuid.UUID | None
    status: str


class IssuePage(BaseModel):
    items: list[IssueRead]
    total: int
    limit: int
    offset: int
    counts: dict[str, int]


class RecomputeRequest(BaseModel):
    start: date = Field(alias="from")
    end: date = Field(alias="to")

    model_config = ConfigDict(populate_by_name=True)


class RecomputeAccepted(BaseModel):
    period: Period
    queued: bool = True


Summary.model_rebuild()
