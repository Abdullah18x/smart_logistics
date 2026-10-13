"""Request and response contracts for the Tracking service."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tracking.milestones import JourneyStatus, Milestone, UpdateType


class JourneyCreate(BaseModel):
    """Opened by the dispatch workflow. Idempotent by ``shipment_id``."""

    shipment_id: uuid.UUID
    reference_no: str = Field(min_length=1, max_length=32)
    courier_id: uuid.UUID
    origin_warehouse_id: uuid.UUID
    destination_city: str = Field(min_length=1, max_length=100)


class JourneyCancel(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class Location(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0)


class TrackingUpdate(BaseModel):
    """One field update from a courier's device.

    ``event_id`` is generated on the device when the update happens, so a
    device that retries (or re-syncs after being offline) sends the same id
    and the update is recorded once. ``occurred_at`` is when it happened, not
    when it arrived: late updates are placed correctly on the timeline.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "event_id": "0f8fad5b-d9cb-469f-a165-70867728950e",
                "shipment_id": "00000000-0000-0000-0000-000000000000",
                "type": "milestone",
                "milestone": "picked_up",
                "occurred_at": "2026-10-12T09:15:00Z",
                "location": {"latitude": 24.8607, "longitude": 67.0011},
            }
        }
    )

    event_id: uuid.UUID
    shipment_id: uuid.UUID
    type: UpdateType
    milestone: Milestone | None = None
    occurred_at: datetime
    location: Location | None = None
    failure_reason: str | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def check_shape(self) -> TrackingUpdate:
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must include a timezone.")
        if self.type is UpdateType.MILESTONE and self.milestone is None:
            raise ValueError("A milestone update needs `milestone`.")
        if self.type is UpdateType.LOCATION and self.location is None:
            raise ValueError("A location update needs `location`.")
        if self.milestone is Milestone.DELIVERY_FAILED and not self.failure_reason:
            raise ValueError("A failed delivery attempt needs `failure_reason`.")
        return self


class UpdateBatch(BaseModel):
    updates: list[TrackingUpdate] = Field(min_length=1)


class UpdateResult(BaseModel):
    event_id: uuid.UUID
    status: str = Field(description="`recorded`, `duplicate` or `rejected`")
    code: str | None = None
    message: str | None = None


class BatchResult(BaseModel):
    results: list[UpdateResult]
    recorded: int
    duplicates: int
    rejected: int


class LocationRead(Location):
    at: datetime


class JourneyRead(BaseModel):
    shipment_id: uuid.UUID
    reference_no: str
    courier_id: uuid.UUID
    origin_warehouse_id: uuid.UUID
    destination_city: str
    status: JourneyStatus
    milestone: Milestone
    milestone_at: datetime
    delivery_attempts: int
    last_location: LocationRead | None = None
    started_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    cancel_reason: str | None = None


class TrackingEventRead(BaseModel):
    event_id: uuid.UUID
    type: UpdateType
    milestone: Milestone | None = None
    occurred_at: datetime
    received_at: datetime
    source: str
    courier_id: uuid.UUID | None = None
    location: Location | None = None
    failure_reason: str | None = None
    note: str | None = None


class JourneyDetail(JourneyRead):
    events: list[TrackingEventRead]


class PublicTracking(BaseModel):
    """What a customer may see: no courier identity, no exact positions."""

    reference_no: str
    milestone: Milestone
    milestone_at: datetime
    destination_city: str
    delivery_attempts: int
    timeline: list[dict]
