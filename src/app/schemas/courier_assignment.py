"""Contracts for courier assignments."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.constants.enums import AssignmentStatus
from app.schemas.courier import CourierSummary


class AssignmentCreate(BaseModel):
    """Manual assignment by an operator.

    The dispatch workflow creates assignments itself; this is the override path
    for when ops needs to direct a specific shipment to a specific courier.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "courier_id": "00000000-0000-0000-0000-000000000000",
                "reason": "Customer requested a specific driver",
            }
        }
    )

    courier_id: uuid.UUID
    reason: str | None = Field(default=None, max_length=500)


class AssignmentResponse(BaseModel):
    """The courier accepting or rejecting an offer."""

    accept: bool
    reason: str | None = Field(default=None, max_length=500, description="Required when rejecting.")


class AssignmentReassign(BaseModel):
    """Moves a shipment to a different courier, closing the current assignment."""

    courier_id: uuid.UUID | None = Field(
        default=None, description="Omit to let the ranker choose the next candidate."
    )
    reason: str = Field(min_length=3, max_length=500)


class AssignmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    shipment_id: uuid.UUID
    courier_id: uuid.UUID
    status: AssignmentStatus
    sequence_no: int
    assigned_at: datetime
    responded_at: datetime | None = None
    completed_at: datetime | None = None
    response_seconds: float | None = None
    reason: str | None = None
    assigned_by: uuid.UUID | None = None


class AssignmentDetail(AssignmentRead):
    """Assignment with the courier resolved, for shipment detail screens."""

    courier: CourierSummary | None = None
