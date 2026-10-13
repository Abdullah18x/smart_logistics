import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from courier.api.deps import (
    AssignmentsDep,
    CourierPrincipal,
    DispatchPrincipal,
    ReassignPrincipal,
    StaffPrincipal,
)
from courier.db import SessionDep
from courier.models import AssignmentStatus
from courier.repository import AssignmentRepository
from courier.schemas import (
    AssignmentRead,
    AssignmentRequest,
    AssignmentResult,
    CourierRead,
    EndAssignmentRequest,
    ReassignRequest,
)
from sl_platform.schemas import Page, Problem

router = APIRouter(prefix="/api/v1/couriers/assignments", tags=["Assignments"])


def _result(outcome) -> AssignmentResult:
    return AssignmentResult(
        assignment=AssignmentRead.model_validate(outcome.assignment),
        courier=CourierRead.model_validate(outcome.courier),
    )


@router.post(
    "",
    response_model=AssignmentResult,
    status_code=status.HTTP_201_CREATED,
    summary="Assign a courier to a shipment (automatic or chosen)",
    responses={
        200: {"model": AssignmentResult, "description": "Already assigned: returned as is"},
        404: {"model": Problem, "description": "Unknown warehouse or courier"},
        409: {
            "model": Problem,
            "description": "no_courier_available, courier_unavailable, already_assigned",
        },
    },
)
async def assign(
    payload: AssignmentRequest,
    principal: DispatchPrincipal,
    assignments: AssignmentsDep,
    session: SessionDep,
    response: Response,
) -> AssignmentResult:
    """Called by the dispatch workflow. Without `courier_id` the best courier is
    chosen: same city as the warehouse, available, below capacity, based at
    this warehouse first, then least loaded. Candidates are locked with
    `SKIP LOCKED`, so concurrent dispatches never double-book a courier.
    Repeating the call for the same shipment returns the same assignment."""
    outcome = await assignments.assign(payload, actor_id=principal.user_id)
    await session.commit()
    if not outcome.created:
        response.status_code = status.HTTP_200_OK
    return _result(outcome)


@router.get("", response_model=Page[AssignmentRead], summary="List assignments")
async def list_assignments(
    _staff: StaffPrincipal,
    session: SessionDep,
    shipment_id: uuid.UUID | None = None,
    courier_id: uuid.UUID | None = None,
    status_: Annotated[AssignmentStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[AssignmentRead]:
    rows, total = await AssignmentRepository(session).list(
        shipment_id=shipment_id,
        courier_id=courier_id,
        status=status_,
        limit=limit,
        offset=offset,
    )
    return Page(
        items=[AssignmentRead.model_validate(a) for a in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/mine", response_model=Page[AssignmentRead], summary="The calling courier's jobs")
async def my_assignments(
    principal: CourierPrincipal,
    session: SessionDep,
    status_: Annotated[AssignmentStatus | None, Query(alias="status")] = AssignmentStatus.ACTIVE,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[AssignmentRead]:
    if principal.courier_id is None:
        return Page(items=[], total=0, limit=limit, offset=offset)
    rows, total = await AssignmentRepository(session).list(
        shipment_id=None,
        courier_id=principal.courier_id,
        status=status_,
        limit=limit,
        offset=offset,
    )
    return Page(
        items=[AssignmentRead.model_validate(a) for a in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/{shipment_id}/reassign",
    response_model=AssignmentResult,
    summary="Hand a shipment to another courier (manual override)",
    responses={404: {"model": Problem}, 409: {"model": Problem}},
)
async def reassign(
    shipment_id: uuid.UUID,
    payload: ReassignRequest,
    principal: ReassignPrincipal,
    assignments: AssignmentsDep,
    session: SessionDep,
) -> AssignmentResult:
    """Releases the current assignment and starts a new one, in one transaction.
    Without `courier_id` the best *other* courier is chosen. Publishes
    `courier.unassigned` then `courier.assigned`; Shipment follows."""
    outcome = await assignments.reassign(
        shipment_id,
        courier_id=payload.courier_id,
        reason=payload.reason,
        actor_id=principal.user_id,
    )
    await session.commit()
    return _result(outcome)


@router.post(
    "/{shipment_id}/release",
    response_model=AssignmentRead | None,
    summary="Release a shipment's courier (dispatch compensation)",
)
async def release(
    shipment_id: uuid.UUID,
    payload: EndAssignmentRequest,
    _p: DispatchPrincipal,
    assignments: AssignmentsDep,
    session: SessionDep,
) -> AssignmentRead | None:
    """A no-op (returns null) when nothing is assigned, so compensation can retry."""
    ended = await assignments.end(shipment_id, completed=False, reason=payload.reason)
    await session.commit()
    return AssignmentRead.model_validate(ended) if ended else None
