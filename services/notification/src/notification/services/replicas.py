"""Contact details Notification keeps from other services' events."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from notification.models import CourierContact, ShipmentRecipient


async def store_recipient(session: AsyncSession, payload: dict) -> bool:
    """Keep who a shipment goes to. False when the event predates recipients
    on ``shipment.created`` (nothing to keep)."""
    recipient = payload.get("recipient")
    if not recipient:
        return False
    values = {
        "shipment_id": uuid.UUID(str(payload["id"])),
        "reference_no": payload["reference_no"],
        "name": recipient["name"],
        "phone": recipient.get("phone"),
        "email": recipient.get("email"),
        "destination_city": payload.get("destination_city"),
        "origin_warehouse_code": payload.get("origin_warehouse_code"),
    }
    statement = pg_insert(ShipmentRecipient).values(**values)
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[ShipmentRecipient.shipment_id],
            set_={k: statement.excluded[k] for k in values if k != "shipment_id"},
        )
    )
    return True


async def store_courier(session: AsyncSession, payload: dict) -> None:
    """Apply a courier profile snapshot unless a newer one is already held."""
    values = {
        "courier_id": uuid.UUID(str(payload["id"])),
        "code": payload["code"],
        "full_name": payload["full_name"],
        "phone": payload.get("phone"),
        "email": payload.get("email"),
        "deleted": bool(payload.get("deleted")),
        "source_updated_at": datetime.fromisoformat(str(payload["updated_at"])),
    }
    statement = pg_insert(CourierContact).values(**values)
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[CourierContact.courier_id],
            set_={k: statement.excluded[k] for k in values if k != "courier_id"},
            where=CourierContact.source_updated_at <= statement.excluded.source_updated_at,
        )
    )
