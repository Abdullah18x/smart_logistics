from typing import Annotated

from fastapi import Depends

from shipment.clients import InventoryClient
from shipment.config import settings
from shipment.db import SessionDep
from shipment.services.shipments import ShipmentService
from sl_platform.auth import Auth, Principal, TokenVerifier
from sl_platform.http import ServiceClient
from sl_platform.roles import UserRole

verifier = TokenVerifier.from_settings(settings)
auth = Auth(verifier)

inventory_client = InventoryClient(
    ServiceClient(
        "inventory", settings.inventory_service_url, timeout=settings.inventory_timeout_seconds
    )
)


def get_inventory() -> InventoryClient:
    return inventory_client


async def get_service(
    session: SessionDep, inventory: Annotated[InventoryClient, Depends(get_inventory)]
) -> ShipmentService:
    return ShipmentService(session, inventory)


ServiceDep = Annotated[ShipmentService, Depends(get_service)]
AnyPrincipal = Annotated[Principal, Depends(auth)]
OpsPrincipal = Annotated[
    Principal, Depends(auth.require(UserRole.ADMIN, UserRole.CUSTOMER_SUPPORT))
]
PackingPrincipal = Annotated[
    Principal, Depends(auth.require(UserRole.ADMIN, UserRole.WAREHOUSE_OPERATOR))
]
