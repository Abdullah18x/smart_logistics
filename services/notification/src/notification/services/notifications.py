"""Creating, sending and resending notifications.

An event becomes notifications in the consumer's transaction: the rows are
written ``pending`` (their ids derived from the event, so a redelivered event
writes nothing new) and queued for sending once the transaction commits.
Sending happens in a Celery task, one notification at a time, with retries; a
sweep re-queues anything left pending, so a lost enqueue or a broker outage
delays a message but never drops it.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta

from prometheus_client import Counter
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from notification.channels import Sender, SendError
from notification.config import Settings
from notification.models import (
    Channel,
    CourierContact,
    Notification,
    NotificationStatus,
    ShipmentRecipient,
    Suppression,
)
from notification.rules import Audience, Contact, Rule, rule_for
from notification.templates import render
from sl_platform.db import utcnow
from sl_platform.errors import ConflictError, NotFoundError
from sl_platform.events import EventEnvelope

logger = logging.getLogger("notification.service")

NAMESPACE = uuid.UUID("2a6b7d0e-5f1c-4b8e-9a3d-7c2e1f0b9a11")

NOTIFICATIONS = Counter(
    "notifications_total",
    "Notifications by outcome: created, sent, retried, failed, suppressed, no_address.",
    ["channel", "template", "outcome"],
)


def notification_id(event_id: uuid.UUID, template: str, channel: Channel) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"{event_id}:{template}:{channel.value}")


@dataclass(frozen=True)
class SendResult:
    status: NotificationStatus
    retry_in: float | None = None  # seconds; set when another attempt is due


class NotificationService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    # --- from events -------------------------------------------------------------------

    async def create_for_event(self, envelope: EventEnvelope) -> list[uuid.UUID]:
        """The notifications an event calls for, written pending. Returns the ids
        written now (none when the event was already handled)."""
        rule = rule_for(envelope.event_type, envelope.payload)
        if rule is None:
            return []
        contact, context = await self._audience(rule, envelope)
        if contact is None:
            logger.info("no %s to notify for %s", rule.audience.value, envelope.event_id)
            return []
        created: list[uuid.UUID] = []
        for channel in rule.channels:
            address = contact.address(channel)
            if not address:
                NOTIFICATIONS.labels(channel.value, rule.template, "no_address").inc()
                continue
            rendered = render(rule.template, channel, context)
            nid = notification_id(envelope.event_id, rule.template, channel)
            row = await self.session.scalar(
                pg_insert(Notification)
                .values(
                    id=nid,
                    event_id=envelope.event_id,
                    event_type=envelope.event_type,
                    shipment_id=_uuid(context.get("shipment_id")),
                    courier_id=_uuid(context.get("courier_id")),
                    template=rule.template,
                    channel=channel,
                    recipient=address,
                    recipient_name=contact.name,
                    subject=rendered.subject,
                    body=rendered.body,
                    html=rendered.html,
                )
                .on_conflict_do_nothing(index_elements=[Notification.id])
                .returning(Notification.id)
            )
            if row is not None:
                created.append(row)
                NOTIFICATIONS.labels(channel.value, rule.template, "created").inc()
        return created

    async def _audience(self, rule: Rule, envelope: EventEnvelope) -> tuple[Contact | None, dict]:
        payload = envelope.payload
        if rule.audience is Audience.COURIER:
            courier = await self.session.get(CourierContact, uuid.UUID(payload["courier_id"]))
            shipment = await self.session.get(ShipmentRecipient, uuid.UUID(payload["shipment_id"]))
            if courier is None or courier.deleted:
                return None, {}
            context = {
                "shipment_id": payload["shipment_id"],
                "courier_id": payload["courier_id"],
                "reference_no": shipment.reference_no if shipment else payload["shipment_id"][:8],
                "warehouse": (shipment.origin_warehouse_code if shipment else None)
                or "the warehouse",
            }
            contact = Contact(
                name=courier.full_name, phone=courier.phone, device=str(courier.courier_id)
            )
            return contact, context

        shipment_id = payload["id"]
        context = {
            "shipment_id": shipment_id,
            "reference_no": payload.get("reference_no", shipment_id[:8]),
            "reason": payload.get("reason"),
            "tracking_url": self.settings.public_tracking_url.format(
                reference_no=payload.get("reference_no", "")
            ),
        }
        if rule.audience is Audience.OPERATIONS:
            return Contact(name="Operations", email=self.settings.ops_email), context
        recipient = await self.session.get(ShipmentRecipient, uuid.UUID(shipment_id))
        if recipient is None:
            return None, context
        context["name"] = recipient.name.split()[0] if recipient.name else "there"
        return Contact(name=recipient.name, email=recipient.email, phone=recipient.phone), context

    # --- sending ---------------------------------------------------------------------

    async def send(self, nid: uuid.UUID, senders: Mapping[Channel, Sender]) -> SendResult:
        """One send attempt, under a row lock so two workers never send the same
        notification at once. Anything not pending is left alone (idempotent)."""
        notification = await self.session.scalar(
            select(Notification).where(Notification.id == nid).with_for_update()
        )
        if notification is None:
            raise NotFoundError("No such notification.")
        if notification.status is not NotificationStatus.PENDING:
            return SendResult(notification.status)
        labels = (notification.channel.value, notification.template)
        if await self._suppressed(notification.channel, notification.recipient):
            notification.status = NotificationStatus.SUPPRESSED
            NOTIFICATIONS.labels(*labels, "suppressed").inc()
            return SendResult(notification.status)

        notification.attempts += 1
        try:
            message_id = await senders[notification.channel].send(notification)
        except SendError as exc:
            notification.last_error = str(exc)[:1000]
            if notification.attempts >= self.settings.max_send_attempts:
                notification.status = NotificationStatus.FAILED
                NOTIFICATIONS.labels(*labels, "failed").inc()
                logger.error("giving up on notification %s: %s", nid, exc)
                return SendResult(notification.status)
            delay = min(5 * 2 ** (notification.attempts - 1), 300)
            notification.next_attempt_at = utcnow() + timedelta(seconds=delay)
            NOTIFICATIONS.labels(*labels, "retried").inc()
            return SendResult(notification.status, retry_in=delay)
        notification.status = NotificationStatus.SENT
        notification.provider_message_id = message_id
        notification.sent_at = utcnow()
        notification.last_error = None
        NOTIFICATIONS.labels(*labels, "sent").inc()
        return SendResult(notification.status)

    async def overdue(self, *, limit: int = 500) -> list[uuid.UUID]:
        """Pending notifications whose attempt is overdue: their task was lost."""
        cutoff = utcnow() - timedelta(seconds=self.settings.requeue_after_seconds)
        return list(
            (
                await self.session.scalars(
                    select(Notification.id)
                    .where(
                        Notification.status == NotificationStatus.PENDING,
                        Notification.next_attempt_at < cutoff,
                    )
                    .order_by(Notification.next_attempt_at)
                    .limit(limit)
                )
            ).all()
        )

    async def _suppressed(self, channel: Channel, address: str) -> bool:
        return (
            await self.session.scalar(
                select(Suppression.id).where(
                    Suppression.channel == channel,
                    func.lower(Suppression.address) == address.lower(),
                )
            )
        ) is not None

    # --- operations ----------------------------------------------------------------

    async def get(self, nid: uuid.UUID) -> Notification:
        notification = await self.session.get(Notification, nid)
        if notification is None:
            raise NotFoundError("No such notification.")
        return notification

    async def list(
        self,
        *,
        shipment_id: uuid.UUID | None = None,
        courier_id: uuid.UUID | None = None,
        recipient: str | None = None,
        status: NotificationStatus | None = None,
        channel: Channel | None = None,
        template: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Notification], int]:
        filters = []
        if shipment_id:
            filters.append(Notification.shipment_id == shipment_id)
        if courier_id:
            filters.append(Notification.courier_id == courier_id)
        if recipient:
            filters.append(func.lower(Notification.recipient) == recipient.lower())
        if status:
            filters.append(Notification.status == status)
        if channel:
            filters.append(Notification.channel == channel)
        if template:
            filters.append(Notification.template == template)
        total = await self.session.scalar(
            select(func.count()).select_from(Notification).where(*filters)
        )
        rows = (
            await self.session.scalars(
                select(Notification)
                .where(*filters)
                .order_by(Notification.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return list(rows), int(total or 0)

    async def resend(self, nid: uuid.UUID) -> Notification:
        """Send a notification again, as a new log entry pointing at the original."""
        original = await self.get(nid)
        if original.status is NotificationStatus.PENDING:
            raise ConflictError("It is still being sent.")
        copy = Notification(
            id=uuid.uuid4(),
            event_id=original.event_id,
            event_type=original.event_type,
            shipment_id=original.shipment_id,
            courier_id=original.courier_id,
            template=original.template,
            channel=original.channel,
            recipient=original.recipient,
            recipient_name=original.recipient_name,
            subject=original.subject,
            body=original.body,
            html=original.html,
            resend_of=original.id,
        )
        self.session.add(copy)
        await self.session.flush()
        return copy

    # --- suppressions ---------------------------------------------------------------

    async def suppress(
        self, channel: Channel, address: str, reason: str | None, actor: uuid.UUID
    ) -> Suppression:
        suppression = Suppression(
            channel=channel, address=address.strip(), reason=reason, created_by=actor
        )
        self.session.add(suppression)
        await self.session.flush()
        return suppression

    async def suppressions(self) -> list[Suppression]:
        return list(
            (await self.session.scalars(select(Suppression).order_by(Suppression.created_at))).all()
        )

    async def unsuppress(self, suppression_id: uuid.UUID) -> None:
        suppression = await self.session.get(Suppression, suppression_id)
        if suppression is None:
            raise NotFoundError("No such suppression.")
        await self.session.delete(suppression)
        await self.session.flush()


def _uuid(value: str | None) -> uuid.UUID | None:
    return uuid.UUID(str(value)) if value else None
