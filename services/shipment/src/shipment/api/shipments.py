import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, status

from shipment.api.deps import AnyPrincipal, OpsPrincipal, PackingPrincipal, ServiceDep
from shipment.db import SessionDep, idempotency
from shipment.models import ServiceLevel, ShipmentPriority, ShipmentStatus
from shipment.schemas import (
    PackageCreate,
    PackageRead,
    PackageUpdate,
    ShipmentCancel,
    ShipmentCreate,
    ShipmentRead,
    ShipmentStatusUpdate,
    ShipmentSummary,
    ShipmentUpdate,
    StatusHistoryRead,
)
from shipment.services.shipments import shipment_id_for
from sl_platform.idempotency import IdempotencyKeyHeader, run_idempotent
from sl_platform.schemas import Page, Problem

router = APIRouter(prefix="/api/v1/shipments", tags=["Shipments"])

FORBIDDEN = {"model": Problem, "description": "Not permitted for your role or scope"}
NOT_FOUND = {"model": Problem, "description": "Shipment not found"}
CONFLICT = {"model": Problem, "description": "Illegal transition, concurrent update, or no stock"}


@router.post(
    "",
    response_model=ShipmentRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        403: FORBIDDEN,
        404: {"model": Problem, "description": "Unknown warehouse or SKU"},
        409: {"model": Problem, "description": "insufficient_stock or warehouse_not_operational"},
        503: {"model": Problem, "description": "Inventory unavailable"},
    },
    summary="Create a shipment and hold its stock",
)
async def create_shipment(
    payload: ShipmentCreate,
    principal: OpsPrincipal,
    shipments: ServiceDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyHeader = None,
):
    """Reserves stock with Inventory first (one synchronous call), then records
    the shipment and `shipment.created` in one transaction. With an
    `Idempotency-Key`, a retry returns the original response and reuses the
    same hold."""
    shipment_id = shipment_id_for(principal.user_id, idempotency_key)

    async def action() -> ShipmentRead:
        return ShipmentRead.model_validate(
            await shipments.create(payload, principal=principal, shipment_id=shipment_id)
        )

    try:
        result = await run_idempotent(
            idempotency,
            session,
            key=idempotency_key,
            owner=str(principal.user_id),
            endpoint="POST /api/v1/shipments",
            payload=payload.model_dump(mode="json"),
            action=action,
            status_code=201,
        )
    except BaseException:
        await shipments.compensate(principal)
        raise
    shipments.creation_committed()
    return result


@router.get("", response_model=Page[ShipmentSummary], summary="List shipments")
async def list_shipments(
    principal: AnyPrincipal,
    shipments: ServiceDep,
    shipment_status: Annotated[ShipmentStatus | None, Query(alias="status")] = None,
    service_level: ServiceLevel | None = None,
    priority: ShipmentPriority | None = None,
    origin_warehouse_id: uuid.UUID | None = None,
    courier_id: uuid.UUID | None = None,
    created_by: uuid.UUID | None = None,
    city: Annotated[str | None, Query(max_length=100)] = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    overdue_only: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[ShipmentSummary]:
    """Row-scoped: operators see their warehouses, couriers their assignments.
    Served entirely from this service's database — no calls to other services."""
    records, total = await shipments.list(
        principal=principal,
        status=shipment_status,
        service_level=service_level,
        priority=priority,
        origin_warehouse_id=origin_warehouse_id,
        courier_id=courier_id,
        created_by=created_by,
        city=city,
        search=search,
        created_from=created_from,
        created_to=created_to,
        overdue_only=overdue_only,
        limit=limit,
        offset=offset,
    )
    return Page[ShipmentSummary](
        items=[ShipmentSummary.model_validate(s) for s in records],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{shipment_id}",
    response_model=ShipmentRead,
    responses={404: NOT_FOUND},
    summary="Fetch a shipment",
)
async def get_shipment(
    shipment_id: uuid.UUID, principal: AnyPrincipal, shipments: ServiceDep
) -> ShipmentRead:
    return ShipmentRead.model_validate(await shipments.get(shipment_id, principal=principal))


@router.get(
    "/{shipment_id}/history",
    response_model=list[StatusHistoryRead],
    responses={404: NOT_FOUND},
    summary="Audit trail of status changes",
)
async def get_history(
    shipment_id: uuid.UUID, principal: AnyPrincipal, shipments: ServiceDep
) -> list[StatusHistoryRead]:
    return [
        StatusHistoryRead.model_validate(h)
        for h in await shipments.history(shipment_id, principal=principal)
    ]


@router.patch(
    "/{shipment_id}",
    response_model=ShipmentRead,
    responses={403: FORBIDDEN, 404: NOT_FOUND, 409: CONFLICT},
    summary="Update a shipment before dispatch",
)
async def update_shipment(
    shipment_id: uuid.UUID,
    payload: ShipmentUpdate,
    principal: OpsPrincipal,
    shipments: ServiceDep,
    session: SessionDep,
) -> ShipmentRead:
    result = ShipmentRead.model_validate(
        await shipments.update(shipment_id, payload, principal=principal)
    )
    await session.commit()
    return result


@router.patch(
    "/{shipment_id}/status",
    response_model=ShipmentRead,
    responses={403: FORBIDDEN, 404: NOT_FOUND, 409: CONFLICT},
    summary="Move a shipment to the next state",
)
async def change_status(
    shipment_id: uuid.UUID,
    payload: ShipmentStatusUpdate,
    principal: AnyPrincipal,
    shipments: ServiceDep,
    session: SessionDep,
    if_match: Annotated[
        int | None, Query(alias="version", description="Expected version (optimistic lock)")
    ] = None,
) -> ShipmentRead:
    """Validated against the state machine; every transition writes an audit
    row and a `shipment.status_changed` event. Concurrent changes to the same
    shipment cannot both apply: the loser gets 409 `concurrent_update`."""
    result = ShipmentRead.model_validate(
        await shipments.change_status(
            shipment_id,
            payload.status,
            principal=principal,
            reason=payload.reason,
            expected_version=if_match,
        )
    )
    await session.commit()
    return result


@router.post(
    "/{shipment_id}/cancel",
    response_model=ShipmentRead,
    responses={403: FORBIDDEN, 404: NOT_FOUND, 409: CONFLICT},
    summary="Cancel a shipment (before dispatch only)",
)
async def cancel_shipment(
    shipment_id: uuid.UUID,
    payload: ShipmentCancel,
    principal: OpsPrincipal,
    shipments: ServiceDep,
    session: SessionDep,
) -> ShipmentRead:
    """Inventory releases the hold when it receives the status event."""
    result = ShipmentRead.model_validate(
        await shipments.change_status(
            shipment_id, ShipmentStatus.CANCELLED, principal=principal, reason=payload.reason
        )
    )
    await session.commit()
    return result


@router.post(
    "/{shipment_id}/packages",
    response_model=PackageRead,
    status_code=status.HTTP_201_CREATED,
    responses={403: FORBIDDEN, 404: NOT_FOUND, 409: CONFLICT},
    summary="Add a parcel",
)
async def add_package(
    shipment_id: uuid.UUID,
    payload: PackageCreate,
    principal: PackingPrincipal,
    shipments: ServiceDep,
    session: SessionDep,
) -> PackageRead:
    result = PackageRead.model_validate(
        await shipments.add_package(shipment_id, payload, principal=principal)
    )
    await session.commit()
    return result


@router.patch(
    "/packages/{package_id}",
    response_model=PackageRead,
    responses={403: FORBIDDEN, 404: {"model": Problem}},
    summary="Update a parcel",
)
async def update_package(
    package_id: uuid.UUID,
    payload: PackageUpdate,
    principal: PackingPrincipal,
    shipments: ServiceDep,
    session: SessionDep,
) -> PackageRead:
    result = PackageRead.model_validate(
        await shipments.update_package(package_id, payload, principal=principal)
    )
    await session.commit()
    return result
