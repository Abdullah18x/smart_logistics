"""How a message leaves: one sender per channel, behind one small interface.

Email is real SMTP — locally into Mailpit, whose inbox is at
http://localhost:8025. SMS and push have no provider in this project; their
senders log the message and return a message id, standing where a provider's
client would (Twilio, FCM...), so swapping one in touches only this file.
"""

from __future__ import annotations

import logging
import uuid
from email.message import EmailMessage
from typing import Protocol

import aiosmtplib

from notification.config import Settings
from notification.models import Channel, Notification

logger = logging.getLogger("notification.channels")


class SendError(RuntimeError):
    """The provider refused or could not be reached; worth retrying."""


class Sender(Protocol):
    async def send(self, notification: Notification) -> str:
        """Deliver the message; return the provider's message id."""


class SmtpSender:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def send(self, notification: Notification) -> str:
        message = EmailMessage()
        message["From"] = self.settings.mail_from
        message["To"] = (
            f"{notification.recipient_name} <{notification.recipient}>"
            if notification.recipient_name
            else notification.recipient
        )
        message["Subject"] = notification.subject or "SmartLogistics"
        message_id = f"<{notification.id}@smartlogistics>"
        message["Message-ID"] = message_id
        message.set_content(notification.body)
        if notification.html:
            message.add_alternative(notification.html, subtype="html")
        try:
            await aiosmtplib.send(
                message,
                hostname=self.settings.smtp_host,
                port=self.settings.smtp_port,
                username=self.settings.smtp_username,
                password=self.settings.smtp_password,
                start_tls=self.settings.smtp_starttls,
                timeout=10,
            )
        except (aiosmtplib.SMTPException, OSError) as exc:
            raise SendError(
                f"SMTP {self.settings.smtp_host}:{self.settings.smtp_port}: {exc}"
            ) from exc
        return message_id


class LoggingSender:
    """Stands in for an SMS or push provider: logs the message and 'accepts' it."""

    def __init__(self, channel: Channel) -> None:
        self.channel = channel

    async def send(self, notification: Notification) -> str:
        message_id = f"mock-{self.channel.value}-{uuid.uuid4().hex[:12]}"
        logger.info(
            "%s to %s: %s (%s)", self.channel.value.upper(), notification.recipient,
            notification.body, message_id,
        )  # fmt: skip
        return message_id


def default_senders(settings: Settings) -> dict[Channel, Sender]:
    return {
        Channel.EMAIL: SmtpSender(settings),
        Channel.SMS: LoggingSender(Channel.SMS),
        Channel.PUSH: LoggingSender(Channel.PUSH),
    }
