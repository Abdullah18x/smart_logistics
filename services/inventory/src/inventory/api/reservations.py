"""The reservation API Shipment calls. Idempotent by ``shipment_id``."""

import uuid

from fastapi import APIRouter, Response, status

from inventory.api.deps import ReservationsDep, ShipmentOperator
from inventory.db import SessionDep
from inventory.schemas import ReleaseRequest, ReservationRequest, ReservationResult
from sl_platform.schemas import Problem

router = APIRouter(prefix="/api/v1/inventory/reservations", tags=["Reservations"])


@router.post(
    "",
    response_model=ReservationResult,
    status_code=status.HTTP_201_CREATED,
    responses={
        200: {
            "model": ReservationResult,
            "description": "Already held: the existing hold is returned",
        },
        404: {"model": Problem, "description": "Unknown warehouse or SKU"},
        409: {
            "model": Problem,
            "description": "insufficient_stock, warehouse_not_operational, reservation_mismatch",
        },
    },
    summary="Hold stock for a shipment (all lines or none)",
)
async def reserve(
    payload: ReservationRequest,
    _principal: ShipmentOperator,
    reservations: ReservationsDep,
    session: SessionDep,
    response: Response,
) -> ReservationResult:
    """Rows are locked in id order, so concurrent requests for the same units
    serialise: the first wins and the second gets `insufficient_stock`. The
    hold stays *unconfirmed* until `shipment.created` arrives, and returns the
    SKU facts Shipment snapshots onto its items."""
    result, created = await reservations.reserve(payload)
    await session.commit()
    if not created:
        response.status_code = status.HTTP_200_OK
    return result


@router.post(
    "/{shipment_id}/confirm", summary="Confirm a hold (normally driven by shipment.created)"
)
async def confirm(
    shipment_id: uuid.UUID, _p: ShipmentOperator, reservations: ReservationsDep, session: SessionDep
) -> dict:
    confirmed = await reservations.confirm(shipment_id)
    await session.commit()
    return {"confirmed": confirmed}


@router.post("/{shipment_id}/commit", summary="Commit held stock (dispatch)")
async def commit(
    shipment_id: uuid.UUID,
    principal: ShipmentOperator,
    reservations: ReservationsDep,
    session: SessionDep,
) -> dict:
    committed = await reservations.commit(shipment_id, actor_id=principal.user_id)
    await session.commit()
    return {"committed": committed}


@router.post("/{shipment_id}/release", summary="Release held stock")
async def release(
    shipment_id: uuid.UUID,
    payload: ReleaseRequest,
    _p: ShipmentOperator,
    reservations: ReservationsDep,
    session: SessionDep,
) -> dict:
    released = await reservations.release(shipment_id, payload.reason)
    await session.commit()
    return {"released": released}
