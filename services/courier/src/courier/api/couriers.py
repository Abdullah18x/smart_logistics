import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from courier.api.deps import (
    AdminPrincipal,
    AnyPrincipal,
    CourierPrincipal,
    CouriersDep,
    StaffPrincipal,
)
from courier.db import SessionDep
from courier.models import Availability, CourierStatus
from courier.repository import CourierRepository
from courier.schemas import (
    AvailabilityUpdate,
    CourierCreate,
    CourierRead,
    CourierStatusUpdate,
    CourierUpdate,
)
from sl_platform.schemas import Page, Problem

router = APIRouter(prefix="/api/v1/couriers", tags=["Couriers"])


@router.post(
    "",
    response_model=CourierRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a courier profile",
    responses={409: {"model": Problem}},
)
async def create_courier(
    payload: CourierCreate, _admin: AdminPrincipal, couriers: CouriersDep, session: SessionDep
) -> CourierRead:
    """`user_id` links the profile to the partner's login; Identity mirrors the
    link from the `courier.created` event, so their token carries `courier_id`."""
    courier = await couriers.create(payload)
    await session.commit()
    return CourierRead.model_validate(courier)


@router.get("", response_model=Page[CourierRead], summary="List couriers")
async def list_couriers(
    _staff: StaffPrincipal,
    session: SessionDep,
    city: str | None = None,
    status_: Annotated[CourierStatus | None, Query(alias="status")] = None,
    availability: Availability | None = None,
    warehouse_id: uuid.UUID | None = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[CourierRead]:
    rows, total = await CourierRepository(session).list(
        city=city,
        status=status_,
        availability=availability,
        warehouse_id=warehouse_id,
        search=search,
        limit=limit,
        offset=offset,
    )
    return Page(
        items=[CourierRead.model_validate(c) for c in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/me", response_model=CourierRead, summary="The calling courier's own profile")
async def my_profile(principal: CourierPrincipal, couriers: CouriersDep) -> CourierRead:
    return CourierRead.model_validate(await couriers.mine(principal))


@router.put(
    "/me/availability",
    response_model=CourierRead,
    summary="Go on shift, take a break or go offline",
)
async def set_my_availability(
    payload: AvailabilityUpdate,
    principal: CourierPrincipal,
    couriers: CouriersDep,
    session: SessionDep,
) -> CourierRead:
    """Only `available` couriers receive new automatic assignments. Going offline
    keeps the shipments already carried."""
    mine = await couriers.mine(principal)
    courier = await couriers.set_availability(mine.id, payload.availability, principal)
    await session.commit()
    return CourierRead.model_validate(courier)


@router.get(
    "/{courier_id}",
    response_model=CourierRead,
    summary="Fetch a courier",
    responses={404: {"model": Problem}},
)
async def get_courier(
    courier_id: uuid.UUID, principal: AnyPrincipal, couriers: CouriersDep
) -> CourierRead:
    return CourierRead.model_validate(await couriers.get(courier_id, principal))


@router.patch("/{courier_id}", response_model=CourierRead, summary="Update a courier profile")
async def update_courier(
    courier_id: uuid.UUID,
    payload: CourierUpdate,
    _admin: AdminPrincipal,
    couriers: CouriersDep,
    session: SessionDep,
) -> CourierRead:
    courier = await couriers.update(courier_id, payload)
    await session.commit()
    return CourierRead.model_validate(courier)


@router.patch(
    "/{courier_id}/status", response_model=CourierRead, summary="Suspend or reactivate a courier"
)
async def set_courier_status(
    courier_id: uuid.UUID,
    payload: CourierStatusUpdate,
    _admin: AdminPrincipal,
    couriers: CouriersDep,
    session: SessionDep,
) -> CourierRead:
    """Suspending also takes the courier offline; shipments they carry stay with
    them until reassigned."""
    courier = await couriers.set_status(courier_id, payload.status, payload.reason)
    await session.commit()
    return CourierRead.model_validate(courier)


@router.put(
    "/{courier_id}/availability",
    response_model=CourierRead,
    summary="Set a courier's availability on their behalf",
)
async def set_availability(
    courier_id: uuid.UUID,
    payload: AvailabilityUpdate,
    principal: AdminPrincipal,
    couriers: CouriersDep,
    session: SessionDep,
) -> CourierRead:
    courier = await couriers.set_availability(courier_id, payload.availability, principal)
    await session.commit()
    return CourierRead.model_validate(courier)


@router.delete(
    "/{courier_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Deactivate (soft delete) a courier",
    responses={409: {"model": Problem, "description": "courier_has_active_assignments"}},
)
async def delete_courier(
    courier_id: uuid.UUID, admin: AdminPrincipal, couriers: CouriersDep, session: SessionDep
) -> Response:
    await couriers.delete(courier_id, admin.user_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{courier_id}/restore", response_model=CourierRead, summary="Restore a deleted courier"
)
async def restore_courier(
    courier_id: uuid.UUID, _admin: AdminPrincipal, couriers: CouriersDep, session: SessionDep
) -> CourierRead:
    courier = await couriers.restore(courier_id)
    await session.commit()
    return CourierRead.model_validate(courier)
