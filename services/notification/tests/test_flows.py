"""From events to notifications: who is told what, on which channel."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from notification.config import settings
from notification.db import database
from notification.models import Channel, Notification, NotificationStatus, ShipmentRecipient
from notification.rules import ON_EVENT, ON_STATUS
from notification.templates import TEMPLATES, render
from tests.conftest import (
    assignment_payload,
    courier_payload,
    created,
    envelope,
    processor,
    status_changed,
)


async def notifications(shipment_id: uuid.UUID | None = None) -> list[Notification]:
    async with database.sessionmaker() as session:
        query = select(Notification).order_by(Notification.template, Notification.channel)
        if shipment_id:
            query = query.where(Notification.shipment_id == shipment_id)
        return list((await session.scalars(query)).all())


def test_every_rule_has_a_template_for_each_of_its_channels():
    for rule in [*ON_STATUS.values(), *ON_EVENT.values()]:
        for channel in rule.channels:
            assert (rule.template, channel) in TEMPLATES, (rule.template, channel)


def test_html_escapes_what_customers_typed_and_text_stays_plain():
    context = {"name": "<script>x</script>", "reference_no": "SL-1", "tracking_url": "http://t"}
    rendered = render("shipment_confirmed", Channel.EMAIL, context)
    assert "<script>" not in rendered.html and "&lt;script&gt;" in rendered.html
    assert rendered.body.startswith("Hi <script>")  # plain text is not HTML
    assert rendered.subject == "We've got your order — SL-1"


def test_sms_messages_fit_in_two_segments():
    context = {"name": "Ayesha", "reference_no": "SL-2026-000123", "reason": "nobody home",
               "tracking_url": "http://localhost:8000/api/v1/tracking/public/SL-2026-000123",
               "warehouse": "KHI-01"}  # fmt: skip
    for (template, channel), _ in TEMPLATES.items():
        if channel is Channel.SMS:
            assert len(render(template, channel, context).body) <= 306, template


async def test_a_new_shipment_keeps_the_recipient_and_confirms_by_email(recorded):
    shipment_id = uuid.uuid4()
    await created(shipment_id)
    (confirmation,) = await notifications(shipment_id)
    assert (confirmation.template, confirmation.channel) == ("shipment_confirmed", Channel.EMAIL)
    assert confirmation.recipient == "ayesha@example.com"
    assert confirmation.status is NotificationStatus.PENDING
    assert "Hi Ayesha," in confirmation.body
    assert confirmation.html and "SmartLogistics" in confirmation.html
    assert recorded.ids == [confirmation.id]  # queued once the transaction committed
    async with database.sessionmaker() as session:
        recipient = await session.get(ShipmentRecipient, shipment_id)
    assert recipient.phone == "+923001234567"


async def test_the_same_event_twice_sends_nothing_new(recorded):
    shipment_id = uuid.uuid4()
    event = await created(shipment_id)
    await processor.process(event)
    assert len(await notifications(shipment_id)) == 1 and len(recorded.ids) == 1


async def test_dispatch_tells_the_customer_by_email_and_sms_with_a_tracking_link():
    shipment_id = uuid.uuid4()
    await created(shipment_id)
    await status_changed(shipment_id, "dispatched", "dispatching")
    sent = {(n.template, n.channel): n for n in await notifications(shipment_id)}
    sms = sent[("shipment_dispatched", Channel.SMS)]
    assert sms.recipient == "+923001234567"
    reference = sms.body.split()[1]
    assert settings.public_tracking_url.format(reference_no=reference) in sms.body
    assert ("shipment_dispatched", Channel.EMAIL) in sent


async def test_each_status_reaches_the_customer_on_its_channels():
    shipment_id = uuid.uuid4()
    await created(shipment_id)
    await status_changed(shipment_id, "picked_up", "dispatched")  # nothing to tell
    await status_changed(shipment_id, "out_for_delivery", "in_transit")
    await status_changed(shipment_id, "delivery_failed", "out_for_delivery", "nobody home")
    await status_changed(shipment_id, "delivered", "out_for_delivery")
    sent = {(n.template, n.channel.value) for n in await notifications(shipment_id)}
    assert sent == {
        ("shipment_confirmed", "email"),
        ("out_for_delivery", "sms"),
        ("delivery_attempt_failed", "sms"),
        ("delivered", "email"),
        ("delivered", "sms"),
    }
    failed = next(
        n for n in await notifications(shipment_id) if n.template == "delivery_attempt_failed"
    )
    assert "(nobody home)" in failed.body


async def test_a_customer_without_email_still_gets_texts():
    shipment_id = uuid.uuid4()
    await created(shipment_id, email=None)
    await status_changed(shipment_id, "delivered", "out_for_delivery")
    assert {n.channel for n in await notifications(shipment_id)} == {Channel.SMS}


async def test_a_failed_dispatch_goes_to_operations_not_the_customer():
    shipment_id = uuid.uuid4()
    await created(shipment_id)
    await status_changed(
        shipment_id, "dispatch_failed", "dispatching", "no_courier_available: nobody in Lahore"
    )
    ops = [n for n in await notifications(shipment_id) if n.template == "dispatch_failed"]
    assert [n.recipient for n in ops] == [settings.ops_email]
    assert "nobody in Lahore" in ops[0].body and ops[0].subject.startswith("[Dispatch failed]")


async def test_a_courier_hears_about_a_new_job_by_push_and_sms():
    shipment_id, courier_id = uuid.uuid4(), uuid.uuid4()
    await created(shipment_id)
    await processor.process(envelope("courier.created", courier_payload(courier_id)))
    await processor.process(
        envelope("courier.assigned", assignment_payload(shipment_id, courier_id), str(shipment_id))
    )
    jobs = [n for n in await notifications(shipment_id) if n.template == "new_assignment"]
    assert {(n.channel, n.recipient) for n in jobs} == {
        (Channel.PUSH, str(courier_id)),
        (Channel.SMS, "+923001110007"),
    }
    assert all(n.courier_id == courier_id for n in jobs)
    assert "KHI-01" in jobs[0].body


async def test_an_unknown_courier_is_not_messaged():
    shipment_id = uuid.uuid4()
    await created(shipment_id)
    await processor.process(
        envelope("courier.assigned", assignment_payload(shipment_id, uuid.uuid4()))
    )
    assert not [n for n in await notifications(shipment_id) if n.template == "new_assignment"]


async def test_status_changes_for_unknown_shipments_are_skipped():
    await status_changed(uuid.uuid4(), "delivered", "out_for_delivery")
    async with database.sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(Notification)) == 0
