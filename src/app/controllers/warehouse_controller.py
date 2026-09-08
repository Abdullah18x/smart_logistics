import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.constants.enums import UserRole, WarehouseStatus, WarehouseType
from app.controllers.dependencies import (
    CurrentUser,
    Scope,
    WarehouseServiceDep,
    require_admin,
    require_roles,
)
from app.models.user import User
from app.schemas.common import Page, Problem
from app.schemas.warehouse import (
    WarehouseCreate,
    WarehouseDetail,
    WarehouseLocation,
    WarehouseRead,
    WarehouseStatusUpdate,
    WarehouseSummary,
    WarehouseUpdate,
)
from app.schemas.warehouse_operating_hours import OperatingHoursRead, WeeklyScheduleUpdate
from app.schemas.warehouse_zone import WarehouseZoneCreate, WarehouseZoneRead, WarehouseZoneUpdate

router = APIRouter(prefix="/warehouses", tags=["Warehouses"])

AdminOnly = Annotated[User, Depends(require_admin)]
OperatorOrAdmin = Annotated[
    User, Depends(require_roles(UserRole.ADMIN, UserRole.WAREHOUSE_OPERATOR))
]

FORBIDDEN = {"model": Problem, "description": "Not permitted for your role or warehouse"}
NOT_FOUND = {"model": Problem, "description": "Warehouse not found"}


@router.get(
    "/locations",
    response_model=list[WarehouseLocation],
    summary="Navigation view of active warehouses",
)
async def list_locations(
    _user: CurrentUser,
    scope: Scope,
    warehouses: WarehouseServiceDep,
    city: Annotated[str | None, Query(max_length=100)] = None,
) -> list[WarehouseLocation]:
    """Everything a courier needs to reach the dock — address, coordinates,
    entrance, geofence, phone and opening hours — and nothing about capacity,
    throughput or internal layout.
    """
    records, _ = await warehouses.list(
        scope=scope, city=city, status=WarehouseStatus.ACTIVE, limit=100
    )
    return [WarehouseLocation.model_validate(w) for w in records]


@router.get("", response_model=Page[WarehouseRead], summary="List warehouses")
async def list_warehouses(
    _user: CurrentUser,
    scope: Scope,
    warehouses: WarehouseServiceDep,
    city: Annotated[str | None, Query(max_length=100)] = None,
    type: Annotated[WarehouseType | None, Query()] = None,
    warehouse_status: Annotated[WarehouseStatus | None, Query(alias="status")] = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[WarehouseRead]:
    """Admins see every facility; operators see only those assigned to them."""
    records, total = await warehouses.list(
        scope=scope,
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


@router.get(
    "/summary",
    response_model=list[WarehouseSummary],
    summary="Compact facility list for support staff",
)
async def list_summary(
    _user: CurrentUser, scope: Scope, warehouses: WarehouseServiceDep
) -> list[WarehouseSummary]:
    records, _ = await warehouses.list(scope=scope, limit=100)
    return [WarehouseSummary.model_validate(w) for w in records]


@router.post(
    "",
    response_model=WarehouseDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create a warehouse",
    responses={403: FORBIDDEN, 409: {"model": Problem, "description": "Code already in use"}},
)
async def create_warehouse(
    payload: WarehouseCreate, _admin: AdminOnly, warehouses: WarehouseServiceDep
) -> WarehouseDetail:
    """Zones and opening hours may be supplied inline, so a facility can be
    created ready to use."""
    return WarehouseDetail.model_validate(await warehouses.create(payload))


@router.get(
    "/{warehouse_id}",
    response_model=WarehouseDetail,
    summary="Fetch a warehouse with zones and schedule",
    responses={404: NOT_FOUND},
)
async def get_warehouse(
    warehouse_id: uuid.UUID,
    _user: CurrentUser,
    scope: Scope,
    warehouses: WarehouseServiceDep,
) -> WarehouseDetail:
    return WarehouseDetail.model_validate(await warehouses.get(warehouse_id, scope=scope))


@router.patch(
    "/{warehouse_id}",
    response_model=WarehouseDetail,
    summary="Update a warehouse",
    responses={403: FORBIDDEN, 404: NOT_FOUND},
)
async def update_warehouse(
    warehouse_id: uuid.UUID,
    payload: WarehouseUpdate,
    _admin: AdminOnly,
    scope: Scope,
    warehouses: WarehouseServiceDep,
) -> WarehouseDetail:
    return WarehouseDetail.model_validate(
        await warehouses.update(warehouse_id, payload, scope=scope)
    )


@router.patch(
    "/{warehouse_id}/status",
    response_model=WarehouseDetail,
    summary="Change operational status",
    responses={403: FORBIDDEN, 404: NOT_FOUND},
)
async def change_status(
    warehouse_id: uuid.UUID,
    payload: WarehouseStatusUpdate,
    _admin: AdminOnly,
    scope: Scope,
    warehouses: WarehouseServiceDep,
) -> WarehouseDetail:
    """Only `active` facilities may originate shipments, so this stops dispatch."""
    return WarehouseDetail.model_validate(
        await warehouses.change_status(warehouse_id, payload, scope=scope)
    )


@router.delete(
    "/{warehouse_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Deactivate a warehouse",
    responses={403: FORBIDDEN, 404: NOT_FOUND},
)
async def delete_warehouse(
    warehouse_id: uuid.UUID,
    _admin: AdminOnly,
    scope: Scope,
    warehouses: WarehouseServiceDep,
) -> None:
    """Soft delete: shipments reference the origin warehouse by id with no
    foreign key, so a hard delete would orphan historical records."""
    await warehouses.delete(warehouse_id, scope=scope)


@router.post(
    "/{warehouse_id}/restore",
    response_model=WarehouseDetail,
    summary="Restore a soft-deleted warehouse",
    responses={403: FORBIDDEN, 404: NOT_FOUND},
)
async def restore_warehouse(
    warehouse_id: uuid.UUID,
    _admin: AdminOnly,
    scope: Scope,
    warehouses: WarehouseServiceDep,
) -> WarehouseDetail:
    """Possible precisely because deletion is soft."""
    return WarehouseDetail.model_validate(await warehouses.restore(warehouse_id, scope=scope))


# --- zones ----------------------------------------------------------------


@router.post(
    "/{warehouse_id}/zones",
    response_model=WarehouseZoneRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a zone",
    responses={
        403: FORBIDDEN,
        404: NOT_FOUND,
        409: {"model": Problem, "description": "Duplicate zone code"},
    },
)
async def add_zone(
    warehouse_id: uuid.UUID,
    payload: WarehouseZoneCreate,
    _user: OperatorOrAdmin,
    scope: Scope,
    warehouses: WarehouseServiceDep,
) -> WarehouseZoneRead:
    return WarehouseZoneRead.model_validate(
        await warehouses.add_zone(warehouse_id, payload, scope=scope)
    )


@router.patch(
    "/zones/{zone_id}",
    response_model=WarehouseZoneRead,
    summary="Update a zone",
    responses={403: FORBIDDEN, 404: {"model": Problem, "description": "Zone not found"}},
)
async def update_zone(
    zone_id: uuid.UUID,
    payload: WarehouseZoneUpdate,
    _user: OperatorOrAdmin,
    scope: Scope,
    warehouses: WarehouseServiceDep,
) -> WarehouseZoneRead:
    return WarehouseZoneRead.model_validate(
        await warehouses.update_zone(zone_id, payload, scope=scope)
    )


@router.delete(
    "/zones/{zone_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a zone",
    responses={403: FORBIDDEN, 404: {"model": Problem, "description": "Zone not found"}},
)
async def delete_zone(
    zone_id: uuid.UUID,
    _user: OperatorOrAdmin,
    scope: Scope,
    warehouses: WarehouseServiceDep,
) -> None:
    await warehouses.delete_zone(zone_id, scope=scope)


# --- operating hours -------------------------------------------------------


@router.get(
    "/{warehouse_id}/operating-hours",
    response_model=list[OperatingHoursRead],
    summary="Weekly schedule",
    responses={404: NOT_FOUND},
)
async def get_hours(
    warehouse_id: uuid.UUID,
    _user: CurrentUser,
    scope: Scope,
    warehouses: WarehouseServiceDep,
) -> list[OperatingHoursRead]:
    rows = await warehouses.list_hours(warehouse_id, scope=scope)
    return [OperatingHoursRead.model_validate(r) for r in rows]


@router.put(
    "/{warehouse_id}/operating-hours",
    response_model=list[OperatingHoursRead],
    summary="Replace the weekly schedule",
    responses={403: FORBIDDEN, 404: NOT_FOUND},
)
async def replace_hours(
    warehouse_id: uuid.UUID,
    payload: WeeklyScheduleUpdate,
    _user: OperatorOrAdmin,
    scope: Scope,
    warehouses: WarehouseServiceDep,
) -> list[OperatingHoursRead]:
    """PUT, not PATCH: a partial schedule is ambiguous — an absent day could
    mean "unchanged" or "closed" — so the full week is always supplied."""
    rows = await warehouses.replace_hours(warehouse_id, payload, scope=scope)
    return [OperatingHoursRead.model_validate(r) for r in rows]
