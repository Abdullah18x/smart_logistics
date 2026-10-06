"""Events Identity publishes (``identity.events``) and consumes."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from identity.models import User
from identity.repository import AssignmentRepository, RefreshTokenRepository
from sl_platform.consumer import EventProcessor
from sl_platform.events import EventEnvelope, Topics
from sl_platform.outbox import add_event

PRODUCER = "identity"


class UserEvents:
    CREATED = "user.created"
    UPDATED = "user.updated"
    ROLE_CHANGED = "user.role_changed"
    STATUS_CHANGED = "user.status_changed"
    DELETED = "user.deleted"
    RESTORED = "user.restored"
    SCOPE_CHANGED = "user.scope_changed"


def user_payload(user: User, **extra) -> dict:
    """Public profile only: credentials and lockout state never leave Identity."""
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "phone": user.phone,
        "role": user.role.value,
        "status": user.status.value,
        "courier_id": user.courier_id,
        "deleted": user.deleted_at is not None,
        **extra,
    }


def emit(session: AsyncSession, event_type: str, user: User, **extra) -> None:
    add_event(
        session,
        topic=Topics.IDENTITY,
        event_type=event_type,
        key=user.id,
        payload=user_payload(user, **extra),
        producer=PRODUCER,
    )


# --- consumed -------------------------------------------------------------------


async def on_warehouse_deleted(session: AsyncSession, event: EventEnvelope) -> None:
    """Replaces the old ``ON DELETE CASCADE``: operators lose a deleted warehouse.

    Their sessions are revoked so the next refresh issues a token without it.
    """
    warehouse_id = uuid.UUID(event.payload["id"])
    affected = await AssignmentRepository(session).delete_for_warehouse(warehouse_id)
    tokens = RefreshTokenRepository(session)
    for user_id in set(affected):
        await tokens.revoke_all(user_id)
        user = await session.get(User, user_id)
        if user is not None:
            emit(session, UserEvents.SCOPE_CHANGED, user, removed_warehouse_id=warehouse_id)


def build_processor(sessionmaker) -> EventProcessor:
    return EventProcessor(
        "identity",
        sessionmaker,
        handlers={"warehouse.deleted": on_warehouse_deleted},
        topics=(Topics.WAREHOUSE,),
    )
