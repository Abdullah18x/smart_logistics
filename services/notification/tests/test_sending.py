"""Sending: retries, giving up, suppressions, and the sweep for lost tasks."""

from __future__ import annotations

import uuid
from datetime import timedelta
from email import message_from_bytes

import pytest
from sqlalchemy import update

from notification import channels
from notification.channels import SendError, SmtpSender
from notification.config import Settings, settings
from notification.db import database
from notification.models import Channel, Notification, NotificationStatus
from notification.services.notifications import NotificationService
from notification.tasks import requeue_overdue, send_notification
from sl_platform.db import utcnow
from tests.conftest import FakeSender, created


async def one(shipment_id) -> Notification:
    async with database.sessionmaker() as session:
        return (await NotificationService(session, settings).list(shipment_id=shipment_id))[0][0]


async def send(nid, senders):
    async with database.sessionmaker() as session:
        result = await NotificationService(session, settings).send(nid, senders)
        await session.commit()
    return result


async def test_a_sent_notification_records_the_provider_id_and_is_not_sent_twice(senders):
    shipment_id = uuid.uuid4()
    await created(shipment_id)
    nid = (await one(shipment_id)).id
    assert (await send(nid, senders)).status is NotificationStatus.SENT
    again = await send(nid, senders)
    assert again.status is NotificationStatus.SENT and len(senders[Channel.EMAIL].sent) == 1
    row = await one(shipment_id)
    assert row.provider_message_id == "fake-1" and row.sent_at and row.attempts == 1


async def test_a_provider_error_backs_off_then_succeeds(senders):
    senders[Channel.EMAIL] = FakeSender(fail=2)
    shipment_id = uuid.uuid4()
    await created(shipment_id)
    nid = (await one(shipment_id)).id
    first = await send(nid, senders)
    second = await send(nid, senders)
    assert (first.retry_in, second.retry_in) == (5, 10)
    row = await one(shipment_id)
    assert row.status is NotificationStatus.PENDING and row.last_error == "provider timed out"
    assert row.next_attempt_at > utcnow()
    assert (await send(nid, senders)).status is NotificationStatus.SENT
    assert (await one(shipment_id)).last_error is None


async def test_it_gives_up_after_the_attempt_limit(senders):
    senders[Channel.EMAIL] = FakeSender(fail=99)
    shipment_id = uuid.uuid4()
    await created(shipment_id)
    nid = (await one(shipment_id)).id
    results = [await send(nid, senders) for _ in range(settings.max_send_attempts)]
    assert results[-1].status is NotificationStatus.FAILED and results[-1].retry_in is None
    assert (await one(shipment_id)).attempts == settings.max_send_attempts


async def test_a_suppressed_address_is_never_messaged(senders):
    shipment_id = uuid.uuid4()
    await created(shipment_id, email="Opted.Out@example.com")
    async with database.sessionmaker() as session:
        await NotificationService(session, settings).suppress(
            Channel.EMAIL, "opted.out@example.com", "asked to stop", uuid.uuid4()
        )
        await session.commit()
    nid = (await one(shipment_id)).id
    assert (await send(nid, senders)).status is NotificationStatus.SUPPRESSED
    assert senders[Channel.EMAIL].sent == []


async def test_the_sweep_requeues_only_what_is_overdue(recorded):
    stuck, fresh = uuid.uuid4(), uuid.uuid4()
    await created(stuck)
    await created(fresh)
    stuck_id = (await one(stuck)).id
    async with database.sessionmaker() as session:
        await session.execute(
            update(Notification)
            .where(Notification.id == stuck_id)
            .values(next_attempt_at=utcnow() - timedelta(minutes=10))
        )
        await session.commit()
    recorded.ids.clear()
    assert await requeue_overdue() == 1
    assert recorded.ids == [stuck_id]


def test_the_send_task_hands_a_backoff_to_celery(monkeypatch):
    from celery.exceptions import Retry

    from notification import tasks
    from notification.services.notifications import SendResult

    async def retry_later(_):
        return SendResult(NotificationStatus.PENDING, retry_in=10)

    monkeypatch.setattr(tasks, "send_one", retry_later)
    with pytest.raises(Retry):
        send_notification.apply(args=(str(uuid.uuid4()),), throw=True)


def test_the_sweep_runs_every_minute():
    from notification.tasks import celery_app

    schedule = celery_app.conf.beat_schedule["requeue-overdue"]
    assert schedule["task"] == "notification.requeue_overdue" and schedule["schedule"] == 60.0
    assert celery_app.conf.task_default_queue == "notification"


class TestSmtp:
    async def test_the_email_has_both_bodies_and_a_stable_message_id(self, monkeypatch):
        captured = {}

        async def fake_send(message, **kwargs):
            captured["message"], captured["kwargs"] = message, kwargs

        monkeypatch.setattr(channels.aiosmtplib, "send", fake_send)
        notification = Notification(
            id=uuid.uuid4(), recipient="ayesha@example.com", recipient_name="Ayesha Khan",
            subject="Delivered", body="plain", html="<p>rich</p>", channel=Channel.EMAIL,
            template="delivered",
        )  # fmt: skip
        message_id = await SmtpSender(Settings(smtp_port=1025)).send(notification)
        parsed = message_from_bytes(captured["message"].as_bytes())
        assert parsed["To"] == "Ayesha Khan <ayesha@example.com>"
        assert parsed["Message-ID"] == message_id == f"<{notification.id}@smartlogistics>"
        assert {p.get_content_type() for p in parsed.walk()} >= {"text/plain", "text/html"}
        assert captured["kwargs"]["port"] == 1025

    async def test_an_unreachable_server_is_a_retryable_send_error(self):
        notification = Notification(
            id=uuid.uuid4(), recipient="a@b.c", subject="s", body="b", channel=Channel.EMAIL,
            template="delivered",
        )  # fmt: skip
        sender = SmtpSender(Settings(smtp_host="127.0.0.1", smtp_port=1))
        with pytest.raises(SendError, match=r"SMTP 127\.0\.0\.1:1"):
            await sender.send(notification)
