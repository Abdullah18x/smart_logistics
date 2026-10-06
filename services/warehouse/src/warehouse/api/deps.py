from typing import Annotated

from fastapi import Depends

from sl_platform.auth import Auth, Principal, TokenVerifier
from sl_platform.roles import UserRole
from warehouse.config import settings
from warehouse.db import SessionDep
from warehouse.services.warehouses import WarehouseService

verifier = TokenVerifier.from_settings(settings)
auth = Auth(verifier)


async def get_service(session: SessionDep) -> WarehouseService:
    return WarehouseService(session)


ServiceDep = Annotated[WarehouseService, Depends(get_service)]
AnyPrincipal = Annotated[Principal, Depends(auth)]
AdminPrincipal = Annotated[Principal, Depends(auth.require(UserRole.ADMIN))]
OperatorOrAdmin = Annotated[
    Principal, Depends(auth.require(UserRole.ADMIN, UserRole.WAREHOUSE_OPERATOR))
]
#: Everyone except couriers, who get the narrower /locations view.
StaffPrincipal = Annotated[
    Principal,
    Depends(auth.require(UserRole.ADMIN, UserRole.CUSTOMER_SUPPORT, UserRole.WAREHOUSE_OPERATOR)),
]
