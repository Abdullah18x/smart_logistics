"""Response envelopes shared by every service."""

from pydantic import BaseModel, Field


class Page[ItemT](BaseModel):
    """A page of results plus the totals a client needs to paginate."""

    items: list[ItemT]
    total: int = Field(description="Total matching records, ignoring pagination.")
    limit: int
    offset: int


class Problem(BaseModel):
    """Error body used for every non-2xx response, by every service."""

    code: str = Field(description="Stable, machine-readable error identifier.")
    message: str = Field(description="Human-readable explanation.")
    detail: dict | list | None = Field(
        default=None, description="Field-level errors, when applicable."
    )


class MessageResponse(BaseModel):
    message: str
