"""Identity mirrors the courier service's profile↔account link as users.courier_id."""

import uuid

from identity.db import database
from identity.events import build_processor
from identity.models import User
from sl_platform.events import EventEnvelope
from sl_platform.roles import UserRole


def profile_event(event_type: str, courier_id, user_id, *, deleted=False) -> EventEnvelope:
    return EventEnvelope(
        event_type=event_type,
        producer="courier",
        key=str(courier_id),
        payload={
            "id": str(courier_id),
            "user_id": str(user_id) if user_id else None,
            "deleted": deleted,
        },
    )


async def courier_id_of(user_id) -> uuid.UUID | None:
    async with database.sessionmaker() as session:
        return (await session.get(User, user_id)).courier_id


async def test_a_new_profile_links_the_courier_account(factory):
    user = await factory.user(UserRole.COURIER)
    courier_id = uuid.uuid4()
    await build_processor(database.sessionmaker).process(
        profile_event("courier.created", courier_id, user.id)
    )
    assert await courier_id_of(user.id) == courier_id


async def test_moving_the_profile_to_another_account_unlinks_the_first(factory):
    first = await factory.user(UserRole.COURIER)
    second = await factory.user(UserRole.COURIER)
    courier_id = uuid.uuid4()
    processor = build_processor(database.sessionmaker)
    await processor.process(profile_event("courier.created", courier_id, first.id))
    await processor.process(profile_event("courier.updated", courier_id, second.id))
    assert await courier_id_of(first.id) is None
    assert await courier_id_of(second.id) == courier_id


async def test_deleting_the_profile_clears_the_link(factory):
    user = await factory.user(UserRole.COURIER)
    courier_id = uuid.uuid4()
    processor = build_processor(database.sessionmaker)
    await processor.process(profile_event("courier.created", courier_id, user.id))
    await processor.process(profile_event("courier.deleted", courier_id, user.id, deleted=True))
    assert await courier_id_of(user.id) is None


async def test_only_courier_accounts_are_linked(factory):
    admin = await factory.user(UserRole.ADMIN)
    await build_processor(database.sessionmaker).process(
        profile_event("courier.created", uuid.uuid4(), admin.id)
    )
    assert await courier_id_of(admin.id) is None
