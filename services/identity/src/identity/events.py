"""Events Identity publishes (``identity.events``) and consumes."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from identity.models import User
from identity.repository import AssignmentRepository, RefreshTokenRepository, UserRepository
from sl_platform.consumer import EventProcessor
from sl_platform.events import EventEnvelope, Topics
from sl_platform.outbox import add_event
from sl_platform.roles import UserRole

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


async def on_courier_profile(session: AsyncSession, event: EventEnvelope) -> None:
    """Mirror the courier service's profile↔account link as ``users.courier_id``.

    The courier service owns the link; Identity keeps a copy only so the access
    token can name the courier profile. A profile moved to another account, or
    deleted, clears the old link. Only accounts with the courier role are linked.
    The change reaches the token at the user's next login or refresh.
    """
    courier_id = uuid.UUID(str(event.payload["id"]))
    raw_user = event.payload.get("user_id")
    target = None if event.payload.get("deleted") or not raw_user else uuid.UUID(str(raw_user))
    for user in await UserRepository(session).linked_to_courier(courier_id):
        if user.id != target:
            user.courier_id = None
            emit(session, UserEvents.UPDATED, user, unlinked_courier_id=courier_id)
    await session.flush()
    if target is None:
        return
    user = await session.get(User, target)
    if user is None or user.role is not UserRole.COURIER or user.courier_id == courier_id:
        return
    user.courier_id = courier_id
    await session.flush()
    emit(session, UserEvents.UPDATED, user, linked_courier_id=courier_id)


def build_processor(sessionmaker) -> EventProcessor:
    handlers = {"warehouse.deleted": on_warehouse_deleted}
    for name in ("courier.created", "courier.updated", "courier.deleted", "courier.restored"):
        handlers[name] = on_courier_profile
    return EventProcessor(
        "identity",
        sessionmaker,
        handlers=handlers,
        topics=(Topics.WAREHOUSE, Topics.COURIER),
    )
