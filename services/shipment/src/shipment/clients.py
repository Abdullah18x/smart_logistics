"""Typed client for the one synchronous dependency Shipment has: Inventory."""

from __future__ import annotations

import uuid
from decimal import Decimal

from pydantic import BaseModel

from sl_platform.http import ServiceClient


class ReservedLine(BaseModel):
    sku_id: uuid.UUID
    quantity: int
    sku_code: str
    sku_name: str
    unit_weight_g: int
    unit_value: Decimal | None = None
    currency: str


class Reservation(BaseModel):
    shipment_id: uuid.UUID
    warehouse_id: uuid.UUID
    status: str
    confirmed: bool
    lines: list[ReservedLine]


class InventoryClient:
    """Calls Inventory with the caller's own token, so Inventory authorises the
    real user. Reserve is idempotent by shipment id, which makes it safe for
    the client's built-in retries."""

    def __init__(self, http: ServiceClient) -> None:
        self.http = http

    async def reserve(
        self,
        *,
        token: str,
        shipment_id: uuid.UUID,
        warehouse_id: uuid.UUID,
        lines: list[tuple[uuid.UUID, int]],
    ) -> Reservation:
        response = await self.http.post(
            "/api/v1/inventory/reservations",
            token=token,
            idempotency_key=str(shipment_id),
            json={
                "shipment_id": str(shipment_id),
                "warehouse_id": str(warehouse_id),
                "lines": [{"sku_id": str(sku), "quantity": qty} for sku, qty in lines],
            },
        )
        return Reservation.model_validate(response.json())

    async def release(self, *, token: str, shipment_id: uuid.UUID, reason: str) -> None:
        await self.http.post(
            f"/api/v1/inventory/reservations/{shipment_id}/release",
            token=token,
            idempotency_key=f"release:{shipment_id}",
            json={"reason": reason},
        )

    async def aclose(self) -> None:
        await self.http.aclose()
