import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from notification.models import Channel, NotificationStatus


class NotificationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    template: str
    channel: Channel
    recipient: str
    recipient_name: str | None
    subject: str | None
    status: NotificationStatus
    attempts: int
    last_error: str | None
    shipment_id: uuid.UUID | None
    courier_id: uuid.UUID | None
    event_type: str | None
    created_at: datetime
    sent_at: datetime | None
    resend_of: uuid.UUID | None


class NotificationRead(NotificationSummary):
    """With the message itself: the text body and, for email, the HTML."""

    body: str
    html: str | None
    event_id: uuid.UUID | None
    provider_message_id: str | None


class SuppressionCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {"channel": "sms", "address": "+923001234567", "reason": "Asked to stop"}
        }
    )

    channel: Channel
    address: str = Field(min_length=3, max_length=320)
    reason: str | None = Field(default=None, max_length=500)


class SuppressionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    channel: Channel
    address: str
    reason: str | None
    created_by: uuid.UUID | None
    created_at: datetime
