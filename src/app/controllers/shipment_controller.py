import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, status
from fastapi.responses import JSONResponse

from app.constants.enums import (
    ServiceLevel,
    ShipmentPriority,
    ShipmentStatus,
    UserRole,
)
from app.controllers.dependencies import (
    CurrentUser,
    IdempotencyServiceDep,
    Scope,
    ShipmentServiceDep,
    require_roles,
)
from app.models.user import User
from app.schemas.common import Page, Problem
from app.schemas.package import PackageCreate, PackageRead, PackageUpdate
from app.schemas.shipment import (
    ShipmentCancel,
    ShipmentCreate,
    ShipmentRead,
    ShipmentStatusUpdate,
    ShipmentSummary,
    ShipmentUpdate,
)

router = APIRouter(prefix="/shipments", tags=["Shipments"])

OpsUser = Annotated[User, Depends(require_roles(UserRole.ADMIN, UserRole.CUSTOMER_SUPPORT))]
PackingUser = Annotated[User, Depends(require_roles(UserRole.ADMIN, UserRole.WAREHOUSE_OPERATOR))]

FORBIDDEN = {"model": Problem, "description": "Not permitted for your role or scope"}
NOT_FOUND = {"model": Problem, "description": "Shipment not found"}
CONFLICT = {"model": Problem, "description": "Illegal state transition or shipment locked"}


@router.post(
    "",
    response_model=ShipmentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a shipment",
    responses={
        403: FORBIDDEN,
        404: {"model": Problem, "description": "Unknown warehouse or SKU"},
        409: {"model": Problem, "description": "Warehouse not active, or SKU inactive"},
    },
)
async def create_shipment(
    payload: ShipmentCreate,
    _user: OpsUser,
    scope: Scope,
    shipments: ShipmentServiceDep,
    idempotency: IdempotencyServiceDep,
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            max_length=255,
            description=(
                "Send a unique value to make retries safe. A repeat with the same key "
                "returns the original response instead of creating a second shipment."
            ),
        ),
    ] = None,
):
    """Creates in `created` status and **holds the stock**.

    The hold is taken in the same transaction, under a row lock, so two requests
    for the same units serialise: the first wins and the second is refused with
    `insufficient_stock` rather than both being accepted and one failing later.
    Holds expire after the configured window if the shipment is never dispatched.

    The reference number comes from a Postgres sequence, so concurrent creates
    cannot collide.
    """
    endpoint = "POST /api/v1/shipments"
    body = payload.model_dump(mode="json")

    if idempotency_key:
        replay = await idempotency.begin(
            key=idempotency_key,
            endpoint=endpoint,
            payload=body,
            user_id=scope.user_id,
        )
        if replay is not None:
            # Same key, same body, already completed: return what we returned then.
            return JSONResponse(status_code=200, content=replay)

    try:
        shipment = ShipmentRead.model_validate(await shipments.create(payload, scope=scope))
    except Exception:
        # Free the key so the client can correct the request and retry.
        if idempotency_key:
            await idempotency.discard(idempotency_key)
        raise

    if idempotency_key:
        await idempotency.complete(
            idempotency_key, status_code=201, body=shipment.model_dump(mode="json")
        )
    return shipment


@router.get("", response_model=Page[ShipmentSummary], summary="List shipments")
async def list_shipments(
    _user: CurrentUser,
    scope: Scope,
    shipments: ShipmentServiceDep,
    shipment_status: Annotated[ShipmentStatus | None, Query(alias="status")] = None,
    service_level: Annotated[ServiceLevel | None, Query()] = None,
    priority: Annotated[ShipmentPriority | None, Query()] = None,
    origin_warehouse_id: Annotated[uuid.UUID | None, Query()] = None,
    courier_id: Annotated[uuid.UUID | None, Query()] = None,
    city: Annotated[str | None, Query(max_length=100)] = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
    created_from: Annotated[datetime | None, Query()] = None,
    created_to: Annotated[datetime | None, Query()] = None,
    overdue_only: Annotated[
        bool | None, Query(description="Past promised delivery and not yet delivered.")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[ShipmentSummary]:
    """Results are scoped to the caller: operators see their warehouses'
    shipments, couriers only their own assignments."""
    records, total = await shipments.list(
        scope=scope,
        status=shipment_status,
        service_level=service_level,
        priority=priority,
        origin_warehouse_id=origin_warehouse_id,
        courier_id=courier_id,
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
    summary="Fetch a shipment",
    responses={404: NOT_FOUND},
)
async def get_shipment(
    shipment_id: uuid.UUID,
    _user: CurrentUser,
    scope: Scope,
    shipments: ShipmentServiceDep,
) -> ShipmentRead:
    return ShipmentRead.model_validate(await shipments.get(shipment_id, scope=scope))


@router.patch(
    "/{shipment_id}",
    response_model=ShipmentRead,
    summary="Update a shipment before dispatch",
    responses={403: FORBIDDEN, 404: NOT_FOUND, 409: CONFLICT},
)
async def update_shipment(
    shipment_id: uuid.UUID,
    payload: ShipmentUpdate,
    _user: OpsUser,
    scope: Scope,
    shipments: ShipmentServiceDep,
) -> ShipmentRead:
    """Editable only while `created`, `ready_for_dispatch` or `dispatch_failed` —
    changing contents after dispatch would misrepresent what was sent."""
    return ShipmentRead.model_validate(await shipments.update(shipment_id, payload, scope=scope))


@router.patch(
    "/{shipment_id}/status",
    response_model=ShipmentRead,
    summary="Move a shipment to the next state",
    responses={403: FORBIDDEN, 404: NOT_FOUND, 409: CONFLICT},
)
async def change_status(
    shipment_id: uuid.UUID,
    payload: ShipmentStatusUpdate,
    _user: CurrentUser,
    scope: Scope,
    shipments: ShipmentServiceDep,
) -> ShipmentRead:
    """Validated against the state machine, which is the single declaration of
    what may follow what. An illegal transition is a 409, never a silent write.

    Each role owns part of the lifecycle: warehouse operators mark shipments
    ready, couriers drive the custody states, support initiates returns.
    """
    return ShipmentRead.model_validate(
        await shipments.change_status(
            shipment_id, payload.status, scope=scope, reason=payload.reason
        )
    )


@router.post(
    "/{shipment_id}/cancel",
    response_model=ShipmentRead,
    summary="Cancel a shipment",
    responses={403: FORBIDDEN, 404: NOT_FOUND, 409: CONFLICT},
)
async def cancel_shipment(
    shipment_id: uuid.UUID,
    payload: ShipmentCancel,
    _user: OpsUser,
    scope: Scope,
    shipments: ShipmentServiceDep,
) -> ShipmentRead:
    """Only possible before the courier takes custody."""
    return ShipmentRead.model_validate(
        await shipments.cancel(shipment_id, payload.reason, scope=scope)
    )


# --- packing ---------------------------------------------------------------


@router.post(
    "/{shipment_id}/packages",
    response_model=PackageRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a parcel to a shipment",
    responses={403: FORBIDDEN, 404: NOT_FOUND, 409: CONFLICT},
)
async def add_package(
    shipment_id: uuid.UUID,
    payload: PackageCreate,
    _user: PackingUser,
    scope: Scope,
    shipments: ShipmentServiceDep,
) -> PackageRead:
    """Packing is the originating warehouse's job. The barcode is issued by the
    system, since it is what scanners and courier devices read."""
    return PackageRead.model_validate(
        await shipments.add_package(shipment_id, payload, scope=scope)
    )


@router.patch(
    "/packages/{package_id}",
    response_model=PackageRead,
    summary="Update a parcel",
    responses={403: FORBIDDEN, 404: {"model": Problem, "description": "Package not found"}},
)
async def update_package(
    package_id: uuid.UUID,
    payload: PackageUpdate,
    _user: PackingUser,
    scope: Scope,
    shipments: ShipmentServiceDep,
) -> PackageRead:
    return PackageRead.model_validate(
        await shipments.update_package(package_id, payload, scope=scope)
    )
