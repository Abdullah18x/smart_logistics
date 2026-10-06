import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from sl_platform.idempotency import IdempotencyKeyHeader, run_idempotent
from sl_platform.schemas import Page, Problem
from warehouse.api.deps import (
    AdminPrincipal,
    AnyPrincipal,
    OperatorOrAdmin,
    ServiceDep,
    StaffPrincipal,
)
from warehouse.db import SessionDep, idempotency
from warehouse.models import WarehouseStatus, WarehouseType
from warehouse.schemas import (
    OperatingHoursRead,
    WarehouseCreate,
    WarehouseDetail,
    WarehouseLocation,
    WarehouseRead,
    WarehouseStatusUpdate,
    WarehouseSummary,
    WarehouseUpdate,
    WarehouseZoneCreate,
    WarehouseZoneRead,
    WarehouseZoneUpdate,
    WeeklyScheduleUpdate,
)

router = APIRouter(prefix="/api/v1/warehouses", tags=["Warehouses"])

FORBIDDEN = {"model": Problem, "description": "Not permitted for your role or warehouse"}
NOT_FOUND = {"model": Problem, "description": "Warehouse not found"}


@router.get("/locations", response_model=list[WarehouseLocation], summary="Courier navigation view")
async def list_locations(
    _principal: AnyPrincipal,
    service: ServiceDep,
    city: Annotated[str | None, Query(max_length=100)] = None,
) -> list[WarehouseLocation]:
    """Address, coordinates, entrance, geofence, phone and hours of active
    facilities — nothing about capacity, contacts or layout."""
    return [WarehouseLocation.model_validate(w) for w in await service.locations(city=city)]


@router.get(
    "", response_model=Page[WarehouseRead], responses={403: FORBIDDEN}, summary="List warehouses"
)
async def list_warehouses(
    principal: StaffPrincipal,
    service: ServiceDep,
    city: Annotated[str | None, Query(max_length=100)] = None,
    type: Annotated[WarehouseType | None, Query()] = None,
    warehouse_status: Annotated[WarehouseStatus | None, Query(alias="status")] = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[WarehouseRead]:
    """Operators see only the warehouses in their token's scope."""
    records, total = await service.list(
        principal=principal,
        city=city,
        type=type,
        status=warehouse_status,
        search=search,
        limit=limit,
        offset=offset,
    )
    return Page[WarehouseRead](
        items=[WarehouseRead.model_validate(w) for w in records],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/summary", response_model=list[WarehouseSummary], summary="Compact facility list")
async def list_summary(principal: StaffPrincipal, service: ServiceDep) -> list[WarehouseSummary]:
    records, _ = await service.list(principal=principal, limit=100)
    return [WarehouseSummary.model_validate(w) for w in records]


@router.post(
    "",
    response_model=WarehouseDetail,
    status_code=status.HTTP_201_CREATED,
    responses={403: FORBIDDEN, 409: {"model": Problem}},
    summary="Create a warehouse",
)
async def create_warehouse(
    payload: WarehouseCreate,
    admin: AdminPrincipal,
    service: ServiceDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyHeader = None,
):
    async def action() -> WarehouseDetail:
        return WarehouseDetail.model_validate(await service.create(payload))

    return await run_idempotent(
        idempotency,
        session,
        key=idempotency_key,
        owner=str(admin.user_id),
        endpoint="POST /api/v1/warehouses",
        payload=payload.model_dump(mode="json"),
        action=action,
        status_code=201,
    )


@router.get(
    "/{warehouse_id}",
    response_model=WarehouseDetail,
    responses={404: NOT_FOUND},
    summary="Fetch a warehouse",
)
async def get_warehouse(
    warehouse_id: uuid.UUID, principal: StaffPrincipal, service: ServiceDep
) -> WarehouseDetail:
    return WarehouseDetail.model_validate(await service.get(warehouse_id, principal=principal))


@router.patch(
    "/{warehouse_id}",
    response_model=WarehouseDetail,
    responses={403: FORBIDDEN, 404: NOT_FOUND},
    summary="Update a warehouse",
)
async def update_warehouse(
    warehouse_id: uuid.UUID,
    payload: WarehouseUpdate,
    admin: AdminPrincipal,
    service: ServiceDep,
    session: SessionDep,
) -> WarehouseDetail:
    result = WarehouseDetail.model_validate(
        await service.update(warehouse_id, payload, principal=admin)
    )
    await session.commit()
    return result


@router.patch(
    "/{warehouse_id}/status",
    response_model=WarehouseDetail,
    responses={403: FORBIDDEN, 404: NOT_FOUND},
    summary="Change operational status",
)
async def change_status(
    warehouse_id: uuid.UUID,
    payload: WarehouseStatusUpdate,
    admin: AdminPrincipal,
    service: ServiceDep,
    session: SessionDep,
) -> WarehouseDetail:
    """Only `active` facilities may originate shipments. Shipment and Inventory
    learn about the change from the `warehouse.status_changed` event."""
    result = WarehouseDetail.model_validate(
        await service.change_status(warehouse_id, payload, principal=admin)
    )
    await session.commit()
    return result


@router.delete(
    "/{warehouse_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={403: FORBIDDEN, 404: NOT_FOUND},
    summary="Deactivate (soft delete) a warehouse",
)
async def delete_warehouse(
    warehouse_id: uuid.UUID, admin: AdminPrincipal, service: ServiceDep, session: SessionDep
) -> Response:
    await service.delete(warehouse_id, principal=admin)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{warehouse_id}/restore",
    response_model=WarehouseDetail,
    responses={403: FORBIDDEN, 404: NOT_FOUND},
    summary="Restore a soft-deleted warehouse",
)
async def restore_warehouse(
    warehouse_id: uuid.UUID, _admin: AdminPrincipal, service: ServiceDep, session: SessionDep
) -> WarehouseDetail:
    result = WarehouseDetail.model_validate(await service.restore(warehouse_id))
    await session.commit()
    return result


# --- zones -------------------------------------------------------------------


@router.post(
    "/{warehouse_id}/zones",
    response_model=WarehouseZoneRead,
    status_code=status.HTTP_201_CREATED,
    responses={403: FORBIDDEN, 404: NOT_FOUND, 409: {"model": Problem}},
    summary="Add a zone",
)
async def add_zone(
    warehouse_id: uuid.UUID,
    payload: WarehouseZoneCreate,
    principal: OperatorOrAdmin,
    service: ServiceDep,
    session: SessionDep,
) -> WarehouseZoneRead:
    result = WarehouseZoneRead.model_validate(
        await service.add_zone(warehouse_id, payload, principal=principal)
    )
    await session.commit()
    return result


@router.patch(
    "/zones/{zone_id}",
    response_model=WarehouseZoneRead,
    responses={403: FORBIDDEN, 404: {"model": Problem}},
    summary="Update a zone",
)
async def update_zone(
    zone_id: uuid.UUID,
    payload: WarehouseZoneUpdate,
    principal: OperatorOrAdmin,
    service: ServiceDep,
    session: SessionDep,
) -> WarehouseZoneRead:
    result = WarehouseZoneRead.model_validate(
        await service.update_zone(zone_id, payload, principal=principal)
    )
    await session.commit()
    return result


@router.delete(
    "/zones/{zone_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={403: FORBIDDEN, 404: {"model": Problem}},
    summary="Remove a zone",
)
async def delete_zone(
    zone_id: uuid.UUID, principal: OperatorOrAdmin, service: ServiceDep, session: SessionDep
) -> Response:
    await service.delete_zone(zone_id, principal=principal)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- operating hours -----------------------------------------------------------


@router.get(
    "/{warehouse_id}/operating-hours",
    response_model=list[OperatingHoursRead],
    responses={404: NOT_FOUND},
    summary="Weekly schedule",
)
async def get_hours(
    warehouse_id: uuid.UUID, principal: AnyPrincipal, service: ServiceDep
) -> list[OperatingHoursRead]:
    return [
        OperatingHoursRead.model_validate(r)
        for r in await service.list_hours(warehouse_id, principal=principal)
    ]


@router.put(
    "/{warehouse_id}/operating-hours",
    response_model=list[OperatingHoursRead],
    responses={403: FORBIDDEN, 404: NOT_FOUND},
    summary="Replace the weekly schedule",
)
async def replace_hours(
    warehouse_id: uuid.UUID,
    payload: WeeklyScheduleUpdate,
    principal: OperatorOrAdmin,
    service: ServiceDep,
    session: SessionDep,
) -> list[OperatingHoursRead]:
    rows = await service.replace_hours(warehouse_id, payload, principal=principal)
    result = [OperatingHoursRead.model_validate(r) for r in rows]
    await session.commit()
    return result
