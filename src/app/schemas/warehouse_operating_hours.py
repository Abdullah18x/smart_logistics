"""Contracts for warehouse operating hours."""

import uuid
from datetime import time

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.constants.enums import Weekday


class OperatingHoursBase(BaseModel):
    day_of_week: Weekday = Field(description="ISO-8601 weekday, Monday = 1.")
    opens_at: time | None = Field(default=None, description="Local time in the warehouse timezone.")
    closes_at: time | None = None
    is_closed: bool = False

    @model_validator(mode="after")
    def check_window(self) -> "OperatingHoursBase":
        """Mirrors the database constraint, so bad input fails at the boundary."""
        if self.is_closed:
            if self.opens_at or self.closes_at:
                raise ValueError("A closed day must not have opening times.")
            return self
        if self.opens_at is None or self.closes_at is None:
            raise ValueError("An open day requires both opens_at and closes_at.")
        if self.closes_at <= self.opens_at:
            raise ValueError("closes_at must be later than opens_at.")
        return self


class OperatingHoursWrite(OperatingHoursBase):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {"day_of_week": 1, "opens_at": "08:00:00", "closes_at": "20:00:00"}
        }
    )


class OperatingHoursRead(OperatingHoursBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    warehouse_id: uuid.UUID


class WeeklyScheduleUpdate(BaseModel):
    """Replaces the whole week at once.

    Partial edits of a schedule are ambiguous — an absent day could mean
    'unchanged' or 'closed' — so the full week is always supplied.
    """

    days: list[OperatingHoursWrite] = Field(min_length=1, max_length=7)

    @model_validator(mode="after")
    def unique_days(self) -> "WeeklyScheduleUpdate":
        seen = [d.day_of_week for d in self.days]
        if len(set(seen)) != len(seen):
            raise ValueError("Each weekday may appear only once.")
        return self
