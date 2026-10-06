"""The local warehouse replica, kept current from ``warehouse.*`` events."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from inventory.models import WarehouseRef
from sl_platform.errors import ConflictError, NotFoundError


class WarehouseNotOperationalError(ConflictError):
    """The warehouse is not active, so stock there cannot be held."""

    code = "warehouse_not_operational"


async def upsert_warehouse(session: AsyncSession, payload: dict) -> None:
    """Apply a warehouse snapshot, ignoring one older than what we hold.

    Events for one warehouse arrive in order on its partition, but a replayed
    topic or a redelivery can still hand us an old snapshot; the
    ``source_updated_at`` guard makes applying it a no-op.
    """
    source_updated_at = datetime.fromisoformat(payload["updated_at"])
    values = {
        "id": uuid.UUID(str(payload["id"])),
        "code": payload["code"],
        "name": payload["name"],
        "status": payload["status"],
        "city": payload["city"],
        "is_deleted": bool(payload.get("deleted")),
        "source_updated_at": source_updated_at,
    }
    statement = pg_insert(WarehouseRef).values(**values)
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[WarehouseRef.id],
            set_={k: statement.excluded[k] for k in values if k != "id"},
            where=WarehouseRef.source_updated_at <= statement.excluded.source_updated_at,
        )
    )


async def require_operational(session: AsyncSession, warehouse_id: uuid.UUID) -> WarehouseRef:
    ref = await session.get(WarehouseRef, warehouse_id)
    if ref is None:
        raise NotFoundError("Unknown warehouse.")
    if not ref.is_operational:
        raise WarehouseNotOperationalError(
            f"Warehouse {ref.code} is {'deleted' if ref.is_deleted else ref.status}; "
            "stock there cannot be held."
        )
    return ref
