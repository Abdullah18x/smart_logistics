"""The hold sweeper on Celery beat."""

import uuid

from inventory.tasks import celery_app, release_expired_holds
from sl_platform.roles import UserRole
from tests.conftest import auth
from tests.test_reservations import RESERVE, request

SUPPORT = auth(UserRole.CUSTOMER_SUPPORT)


async def test_the_sweep_releases_only_expired_unconfirmed_holds(client, factory):
    warehouse = await factory.warehouse()
    sku = await factory.sku()
    item = await factory.stock(warehouse, sku, on_hand=10)
    abandoned, fresh = uuid.uuid4(), uuid.uuid4()
    for shipment_id, qty in ((abandoned, 3), (fresh, 2)):
        await client.post(
            RESERVE, json=request(warehouse, (sku, qty), shipment_id=shipment_id), headers=SUPPORT
        )
    await factory.age_holds(abandoned)
    assert await release_expired_holds() == 1
    assert (await factory.item(item.id)).reserved_qty == 2
    assert await release_expired_holds() == 0


def test_the_sweep_is_on_beat():
    schedule = celery_app.conf.beat_schedule["release-expired-holds"]
    assert schedule["task"] == "inventory.release_expired_holds"
    assert schedule["schedule"] == 300.0
    assert celery_app.conf.task_default_queue == "inventory"
