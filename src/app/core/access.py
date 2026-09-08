"""Row-level access scoping.

ADR-009 authorises in two layers: a role gate decides whether an endpoint may
be called at all, and this decides which rows the caller may see or touch.
Blanket role checks alone would let a courier enumerate another courier's work.
"""

import uuid
from dataclasses import dataclass

from app.constants.enums import UserRole


@dataclass(frozen=True)
class AccessScope:
    """What subset of rows the caller may act on.

    ``warehouse_ids`` and ``courier_id`` are ``None`` when unrestricted, which
    is not the same as an empty list — an operator with no assignments has an
    empty list and therefore sees nothing.
    """

    role: UserRole
    user_id: uuid.UUID
    warehouse_ids: list[uuid.UUID] | None = None
    courier_id: uuid.UUID | None = None

    @property
    def is_admin(self) -> bool:
        return self.role is UserRole.ADMIN

    @property
    def sees_all_warehouses(self) -> bool:
        return self.warehouse_ids is None

    def may_touch_warehouse(self, warehouse_id: uuid.UUID) -> bool:
        return self.warehouse_ids is None or warehouse_id in self.warehouse_ids
