"""The local warehouse replica, kept current from ``warehouse.*`` events."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from courier.models import WarehouseRef
from sl_platform.errors import ConflictError, NotFoundError


class WarehouseNotOperationalError(ConflictError):
    code = "warehouse_not_operational"


async def upsert_warehouse(session: AsyncSession, payload: dict) -> None:
    """Apply a snapshot unless we already hold a newer one."""
    values = {
        "id": uuid.UUID(str(payload["id"])),
        "code": payload["code"],
        "name": payload["name"],
        "status": payload["status"],
        "city": payload["city"],
        "is_deleted": bool(payload.get("deleted")),
        "source_updated_at": datetime.fromisoformat(str(payload["updated_at"])),
    }
    statement = pg_insert(WarehouseRef).values(**values)
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[WarehouseRef.id],
            set_={k: statement.excluded[k] for k in values if k != "id"},
            where=WarehouseRef.source_updated_at <= statement.excluded.source_updated_at,
        )
    )


async def require_warehouse(session: AsyncSession, warehouse_id: uuid.UUID) -> WarehouseRef:
    ref = await session.get(WarehouseRef, warehouse_id)
    if ref is None:
        raise NotFoundError("Unknown warehouse.")
    if ref.is_deleted or ref.status != "active":
        raise WarehouseNotOperationalError(f"Warehouse {ref.code} is not operational.")
    return ref
