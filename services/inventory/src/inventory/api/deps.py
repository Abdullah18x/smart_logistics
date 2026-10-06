from datetime import timedelta
from typing import Annotated

from fastapi import Depends

from inventory.config import settings
from inventory.db import SessionDep
from inventory.services.reservations import ReservationService
from inventory.services.stock import CatalogService, StockService
from sl_platform.auth import Auth, Principal, TokenVerifier
from sl_platform.roles import UserRole

verifier = TokenVerifier.from_settings(settings)
auth = Auth(verifier)


async def get_reservations(session: SessionDep) -> ReservationService:
    return ReservationService(
        session, unconfirmed_hold=timedelta(minutes=settings.unconfirmed_hold_minutes)
    )


async def get_catalog(session: SessionDep) -> CatalogService:
    return CatalogService(session)


async def get_stock(session: SessionDep) -> StockService:
    return StockService(session)


ReservationsDep = Annotated[ReservationService, Depends(get_reservations)]
CatalogDep = Annotated[CatalogService, Depends(get_catalog)]
StockDep = Annotated[StockService, Depends(get_stock)]

AnyPrincipal = Annotated[Principal, Depends(auth)]
AdminPrincipal = Annotated[Principal, Depends(auth.require(UserRole.ADMIN))]
StockViewer = Annotated[
    Principal,
    Depends(auth.require(UserRole.ADMIN, UserRole.WAREHOUSE_OPERATOR, UserRole.CUSTOMER_SUPPORT)),
]
StockManager = Annotated[
    Principal, Depends(auth.require(UserRole.ADMIN, UserRole.WAREHOUSE_OPERATOR))
]
#: Whoever may create or cancel shipments may hold and release stock for them.
ShipmentOperator = Annotated[
    Principal, Depends(auth.require(UserRole.ADMIN, UserRole.CUSTOMER_SUPPORT))
]
