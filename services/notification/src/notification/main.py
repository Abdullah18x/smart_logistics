"""Notification HTTP process: ``uvicorn notification.main:app``."""

from notification.api import notifications
from notification.api.deps import verifier
from notification.config import settings
from notification.db import database
from notification.models import CONSTRAINT_MESSAGES
from sl_platform.app import create_service_app

app = create_service_app(
    settings=settings,
    database=database,
    title="SmartLogistics Notification",
    description=(
        "Tells customers, couriers and operations when shipments move — email over SMTP "
        "(Mailpit locally, inbox at :8025), SMS and push through mock providers. Every "
        "message is in the delivery log with what was sent and whether it arrived."
    ),
    # Suppressions first: their fixed path must win over /{notification_id}.
    routers=[notifications.suppressions, notifications.router],
    tags_metadata=[
        {"name": "Notifications", "description": "The delivery log, and resending."},
        {"name": "Suppressions", "description": "Addresses not to message."},
    ],
    constraint_messages=CONSTRAINT_MESSAGES,
    on_startup=[verifier.try_warm_up],
)
