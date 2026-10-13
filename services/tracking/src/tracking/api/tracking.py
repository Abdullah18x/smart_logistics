import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from sl_platform.errors import ConstraintViolationError
from sl_platform.schemas import Problem
from tracking.api.deps import AnyPrincipal, CourierPrincipal, JourneysDep, WorkflowPrincipal
from tracking.config import settings
from tracking.schemas import (
    BatchResult,
    JourneyCancel,
    JourneyCreate,
    JourneyDetail,
    JourneyRead,
    PublicTracking,
    TrackingEventRead,
    TrackingUpdate,
    UpdateBatch,
    UpdateResult,
)

router = APIRouter(prefix="/api/v1/tracking", tags=["Tracking"])


def journey_read(doc: dict) -> dict:
    return {**doc, "shipment_id": doc["_id"]}


def event_read(doc: dict) -> TrackingEventRead:
    return TrackingEventRead.model_validate({**doc, "event_id": doc["_id"]})


@router.post(
    "/journeys",
    response_model=JourneyRead,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"model": JourneyRead, "description": "Already open: returned as is"}},
    summary="Open a shipment's journey (dispatch workflow)",
)
async def open_journey(
    payload: JourneyCreate, _p: WorkflowPrincipal, journeys: JourneysDep, response: Response
) -> JourneyRead:
    journey, created = await journeys.open(payload)
    if not created:
        response.status_code = status.HTTP_200_OK
    return JourneyRead.model_validate(journey_read(journey))


@router.post(
    "/journeys/{shipment_id}/cancel",
    response_model=JourneyRead,
    responses={404: {"model": Problem}, 409: {"model": Problem}},
    summary="Cancel a journey (dispatch compensation)",
)
async def cancel_journey(
    shipment_id: uuid.UUID, payload: JourneyCancel, _p: WorkflowPrincipal, journeys: JourneysDep
) -> JourneyRead:
    return JourneyRead.model_validate(
        journey_read(await journeys.cancel(shipment_id, payload.reason))
    )


@router.get(
    "/journeys/{shipment_id}",
    response_model=JourneyDetail,
    responses={404: {"model": Problem}},
    summary="A shipment's journey with its timeline",
)
async def get_journey(
    shipment_id: uuid.UUID,
    principal: AnyPrincipal,
    journeys: JourneysDep,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> JourneyDetail:
    journey = await journeys.get(shipment_id, principal)
    events = await journeys.events(shipment_id, limit=limit)
    return JourneyDetail.model_validate(
        {**journey_read(journey), "events": [event_read(e) for e in events]}
    )


@router.post(
    "/updates",
    response_model=UpdateResult,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Report a field update (courier)",
)
async def report_update(
    payload: TrackingUpdate, principal: CourierPrincipal, journeys: JourneysDep
) -> UpdateResult:
    """Pickup, progress, delivery attempts, location. Safe to retry: the same
    `event_id` is recorded once and comes back as `duplicate`. Milestones reach
    the shipment through the delivery workflow, which validates them."""
    return await journeys.record(payload, principal)


@router.post(
    "/updates/batch",
    response_model=BatchResult,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Report many field updates at once (offline sync)",
)
async def report_batch(
    payload: UpdateBatch, principal: CourierPrincipal, journeys: JourneysDep
) -> BatchResult:
    """For a device coming back online: each update is processed on its own and
    gets its own result, in occurred order; duplicates and rejections never
    fail the batch."""
    if len(payload.updates) > settings.max_batch_size:
        raise ConstraintViolationError(f"At most {settings.max_batch_size} updates per batch.")
    ordered = sorted(payload.updates, key=lambda u: u.occurred_at)
    results = [await journeys.record(update, principal) for update in ordered]
    return BatchResult(
        results=results,
        recorded=sum(r.status == "recorded" for r in results),
        duplicates=sum(r.status == "duplicate" for r in results),
        rejected=sum(r.status == "rejected" for r in results),
    )


@router.get(
    "/public/{reference_no}",
    response_model=PublicTracking,
    responses={404: {"model": Problem}},
    summary="Public tracking page data (no login)",
)
async def public_tracking(reference_no: str, journeys: JourneysDep) -> PublicTracking:
    """What a customer sees with their tracking number: milestones and times,
    never the courier's identity or position."""
    journey = await journeys.by_reference(reference_no)
    events = await journeys.events(uuid.UUID(journey["_id"]))
    return PublicTracking(
        reference_no=journey["reference_no"],
        milestone=journey["milestone"],
        milestone_at=journey["milestone_at"],
        destination_city=journey["destination_city"],
        delivery_attempts=journey["delivery_attempts"],
        timeline=[
            {"milestone": e["milestone"], "at": e["occurred_at"]}
            for e in events
            if e["type"] == "milestone"
        ],
    )
