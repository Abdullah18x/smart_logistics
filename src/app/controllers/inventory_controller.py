import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.controllers.dependencies import (
    InventoryServiceDep,
    Scope,
    SessionDep,
    require_roles,
)
from app.core.enums import ReservationStatus, UserRole
from app.models.inventory_item import InventoryItem
from app.models.inventory_reservation import InventoryReservation
from app.models.user import User
from app.schemas.common import Page, Problem
from app.schemas.inventory import InventoryItemRead, ReservationRead

router = APIRouter(prefix="/inventory", tags=["Inventory"])

StockViewer = Annotated[
    User,
    Depends(require_roles(UserRole.ADMIN, UserRole.WAREHOUSE_OPERATOR, UserRole.CUSTOMER_SUPPORT)),
]
StockManager = Annotated[User, Depends(require_roles(UserRole.ADMIN, UserRole.WAREHOUSE_OPERATOR))]

FORBIDDEN = {"model": Problem, "description": "Not permitted for your role or warehouse"}


@router.get("", response_model=Page[InventoryItemRead], summary="List stock levels")
async def list_stock(
    _user: StockViewer,
    scope: Scope,
    session: SessionDep,
    warehouse_id: Annotated[uuid.UUID | None, Query()] = None,
    sku_id: Annotated[uuid.UUID | None, Query()] = None,
    below_reorder_level: Annotated[
        bool | None, Query(description="Only positions at or under their reorder threshold.")
    ] = None,
    in_stock_only: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[InventoryItemRead]:
    """`available_qty` is a generated column — on hand minus held — so it can
    never disagree with its inputs."""
    filters = []
    if warehouse_id is not None:
        filters.append(InventoryItem.warehouse_id == warehouse_id)
    if sku_id is not None:
        filters.append(InventoryItem.sku_id == sku_id)
    if below_reorder_level:
        filters.append(InventoryItem.available_qty <= InventoryItem.reorder_level)
    if in_stock_only:
        filters.append(InventoryItem.available_qty > 0)
    if scope.role is UserRole.WAREHOUSE_OPERATOR:
        filters.append(InventoryItem.warehouse_id.in_(scope.warehouse_ids or []))

    from sqlalchemy import func

    total = await session.scalar(select(func.count()).select_from(InventoryItem).where(*filters))
    rows = await session.scalars(
        select(InventoryItem)
        .where(*filters)
        .order_by(InventoryItem.warehouse_id, InventoryItem.sku_id)
        .limit(limit)
        .offset(offset)
    )
    return Page[InventoryItemRead](
        items=[InventoryItemRead.model_validate(i) for i in rows.all()],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/reservations",
    response_model=list[ReservationRead],
    summary="List stock holds",
)
async def list_reservations(
    _user: StockViewer,
    scope: Scope,
    session: SessionDep,
    shipment_id: Annotated[uuid.UUID | None, Query()] = None,
    reservation_status: Annotated[ReservationStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ReservationRead]:
    """Shows what stock is held, for whom, and when the hold lapses."""
    filters = []
    if shipment_id is not None:
        filters.append(InventoryReservation.shipment_id == shipment_id)
    if reservation_status is not None:
        filters.append(InventoryReservation.status == reservation_status)
    if scope.role is UserRole.WAREHOUSE_OPERATOR:
        filters.append(InventoryReservation.warehouse_id.in_(scope.warehouse_ids or []))

    rows = await session.scalars(
        select(InventoryReservation)
        .where(*filters)
        .order_by(InventoryReservation.created_at.desc())
        .limit(limit)
    )
    return [ReservationRead.model_validate(r) for r in rows.all()]


@router.post(
    "/release-expired",
    summary="Release stock holds that have lapsed",
    responses={403: FORBIDDEN},
)
async def release_expired(
    _user: StockManager,
    scope: Scope,
    inventory: InventoryServiceDep,
    warehouse_id: Annotated[uuid.UUID | None, Query()] = None,
) -> dict:
    """Expired holds on contended stock are released automatically when someone
    tries to reserve it. This sweeps the rest, and is what Celery beat will call
    on a schedule in Phase 3 — correctness does not depend on it running.
    """
    if scope.role is UserRole.WAREHOUSE_OPERATOR and warehouse_id is None:
        # An operator may only sweep their own facilities.
        released = 0
        for scoped_id in scope.warehouse_ids or []:
            released += await inventory.release_expired(scoped_id)
    else:
        released = await inventory.release_expired(warehouse_id)
    await inventory.session.commit()
    return {"released": released}
