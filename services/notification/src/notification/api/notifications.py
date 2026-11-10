import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from notification.api.deps import (
    AdminPrincipal,
    CourierPrincipal,
    NotificationsDep,
    StaffPrincipal,
)
from notification.db import SessionDep
from notification.models import Channel, NotificationStatus
from notification.queue import enqueue
from notification.schemas import (
    NotificationRead,
    NotificationSummary,
    SuppressionCreate,
    SuppressionRead,
)
from sl_platform.schemas import Page, Problem

router = APIRouter(prefix="/api/v1/notifications", tags=["Notifications"])
suppressions = APIRouter(prefix="/api/v1/notifications/suppressions", tags=["Suppressions"])


@router.get("", response_model=Page[NotificationSummary], summary="The delivery log")
async def list_notifications(
    _staff: StaffPrincipal,
    notifications: NotificationsDep,
    shipment_id: uuid.UUID | None = None,
    recipient: Annotated[str | None, Query(max_length=320)] = None,
    status_: Annotated[NotificationStatus | None, Query(alias="status")] = None,
    channel: Channel | None = None,
    template: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[NotificationSummary]:
    """Who was told what about a shipment, on which channel, and whether it
    arrived. Newest first."""
    rows, total = await notifications.list(
        shipment_id=shipment_id,
        recipient=recipient,
        status=status_,
        channel=channel,
        template=template,
        limit=limit,
        offset=offset,
    )
    return Page(
        items=[NotificationSummary.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/mine", response_model=Page[NotificationSummary], summary="The calling courier's")
async def my_notifications(
    principal: CourierPrincipal,
    notifications: NotificationsDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[NotificationSummary]:
    rows, total = await notifications.list(
        courier_id=principal.courier_id, limit=limit, offset=offset
    )
    return Page(
        items=[NotificationSummary.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{notification_id}",
    response_model=NotificationRead,
    responses={404: {"model": Problem}},
    summary="One notification, with the message as sent",
)
async def get_notification(
    notification_id: uuid.UUID, _staff: StaffPrincipal, notifications: NotificationsDep
) -> NotificationRead:
    return NotificationRead.model_validate(await notifications.get(notification_id))


@router.post(
    "/{notification_id}/resend",
    response_model=NotificationRead,
    status_code=status.HTTP_202_ACCEPTED,
    responses={404: {"model": Problem}, 409: {"model": Problem}},
    summary="Send a notification again",
)
async def resend(
    notification_id: uuid.UUID,
    _staff: StaffPrincipal,
    notifications: NotificationsDep,
    session: SessionDep,
) -> NotificationRead:
    """Creates a new entry (``resend_of`` the original) and queues it — after a
    customer says they never got it, or after fixing a failed one's address."""
    copy = await notifications.resend(notification_id)
    await session.commit()
    await enqueue([copy.id])
    return NotificationRead.model_validate(copy)


@suppressions.get("", response_model=list[SuppressionRead], summary="Suppressed addresses")
async def list_suppressions(
    _staff: StaffPrincipal, notifications: NotificationsDep
) -> list[SuppressionRead]:
    return [SuppressionRead.model_validate(s) for s in await notifications.suppressions()]


@suppressions.post(
    "",
    response_model=SuppressionRead,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"model": Problem}},
    summary="Stop messaging an address on a channel",
)
async def suppress(
    payload: SuppressionCreate,
    principal: StaffPrincipal,
    notifications: NotificationsDep,
    session: SessionDep,
) -> SuppressionRead:
    """Opt-outs, wrong numbers, bounced addresses. Checked at send time, so it
    also stops messages already queued."""
    row = await notifications.suppress(
        payload.channel, payload.address, payload.reason, principal.user_id
    )
    await session.commit()
    return SuppressionRead.model_validate(row)


@suppressions.delete(
    "/{suppression_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"model": Problem}},
    summary="Allow an address again",
)
async def unsuppress(
    suppression_id: uuid.UUID,
    _admin: AdminPrincipal,
    notifications: NotificationsDep,
    session: SessionDep,
) -> Response:
    await notifications.unsuppress(suppression_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
