import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from inventory.api.deps import ReservationsDep, StockDep, StockManager, StockViewer
from inventory.db import SessionDep
from inventory.models import ReservationStatus
from inventory.schemas import (
    InventoryItemCreate,
    InventoryItemRead,
    ReservationRead,
    StockAdjustment,
    StockMovementRead,
)
from sl_platform.schemas import Page, Problem

router = APIRouter(prefix="/api/v1/inventory", tags=["Inventory"])
FORBIDDEN = {"model": Problem, "description": "Not permitted for your role or warehouse"}


@router.get("", response_model=Page[InventoryItemRead], summary="List stock levels")
async def list_stock(
    principal: StockViewer,
    stock: StockDep,
    warehouse_id: uuid.UUID | None = None,
    sku_id: uuid.UUID | None = None,
    below_reorder_level: bool | None = None,
    in_stock_only: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[InventoryItemRead]:
    """Operators see only their warehouses. `available_qty` is a generated column."""
    rows, total = await stock.list_items(
        principal=principal,
        warehouse_id=warehouse_id,
        sku_id=sku_id,
        below_reorder_level=below_reorder_level,
        in_stock_only=in_stock_only,
        limit=limit,
        offset=offset,
    )
    return Page[InventoryItemRead](
        items=[InventoryItemRead.model_validate(i) for i in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "",
    response_model=InventoryItemRead,
    status_code=status.HTTP_201_CREATED,
    responses={403: FORBIDDEN, 404: {"model": Problem}, 409: {"model": Problem}},
    summary="Open a stock position",
)
async def open_position(
    payload: InventoryItemCreate, principal: StockManager, stock: StockDep, session: SessionDep
) -> InventoryItemRead:
    result = InventoryItemRead.model_validate(
        await stock.open_position(payload, principal=principal)
    )
    await session.commit()
    return result


@router.post(
    "/{item_id}/adjustments",
    response_model=InventoryItemRead,
    responses={403: FORBIDDEN, 404: {"model": Problem}, 422: {"model": Problem}},
    summary="Adjust on-hand stock (receipt, count, write-off)",
)
async def adjust(
    item_id: uuid.UUID,
    payload: StockAdjustment,
    principal: StockManager,
    stock: StockDep,
    session: SessionDep,
) -> InventoryItemRead:
    """Every change leaves a ledger entry. A result below the held quantity is refused."""
    item = await stock.adjust(item_id, payload, principal=principal)
    await session.commit()
    return InventoryItemRead.model_validate(item)


@router.get(
    "/{item_id}/movements",
    response_model=list[StockMovementRead],
    summary="Ledger for one position",
)
async def movements(
    item_id: uuid.UUID,
    principal: StockViewer,
    stock: StockDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[StockMovementRead]:
    return [
        StockMovementRead.model_validate(m)
        for m in await stock.movements(item_id, principal=principal, limit=limit)
    ]


@router.get("/reservations", response_model=list[ReservationRead], summary="List stock holds")
async def list_reservations(
    principal: StockViewer,
    stock: StockDep,
    shipment_id: uuid.UUID | None = None,
    reservation_status: Annotated[ReservationStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ReservationRead]:
    rows = await stock.list_reservations(
        principal=principal, shipment_id=shipment_id, status=reservation_status, limit=limit
    )
    return [ReservationRead.model_validate(r) for r in rows]


@router.post(
    "/release-expired", responses={403: FORBIDDEN}, summary="Release lapsed unconfirmed holds"
)
async def release_expired(
    principal: StockManager,
    reservations: ReservationsDep,
    session: SessionDep,
    warehouse_id: uuid.UUID | None = None,
) -> dict:
    """Operators can only sweep their own warehouses — a `warehouse_id` outside
    their scope is refused (the monolith let it through)."""
    if principal.is_admin:
        scope = [warehouse_id] if warehouse_id else None
    else:
        allowed = list(principal.warehouse_ids)
        if warehouse_id is not None and warehouse_id not in allowed:
            from sl_platform.errors import PermissionDeniedError

            raise PermissionDeniedError("You may only release holds in your own warehouses.")
        scope = [warehouse_id] if warehouse_id else allowed
    released = await reservations.release_expired(scope)
    await session.commit()
    return {"released": released}
