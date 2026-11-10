from typing import Annotated

from fastapi import Depends

from analytics.config import settings
from analytics.db import SessionDep
from analytics.services.reports import Reports
from sl_platform.auth import Auth, Principal, TokenVerifier
from sl_platform.cache import Cache
from sl_platform.revocation import TokenDenylist
from sl_platform.roles import UserRole

verifier = TokenVerifier.from_settings(settings)
auth = Auth(verifier, TokenDenylist.from_settings(settings))
#: Short-lived answers for /summary (dashboards poll it); None without Redis.
cache = Cache.from_settings(settings)


async def get_reports(session: SessionDep) -> Reports:
    return Reports(session)


ReportsDep = Annotated[Reports, Depends(get_reports)]

#: Who may read metrics. Warehouse operators see their own warehouses only.
ReaderPrincipal = Annotated[
    Principal,
    Depends(auth.require(UserRole.ADMIN, UserRole.CUSTOMER_SUPPORT, UserRole.WAREHOUSE_OPERATOR)),
]
StaffPrincipal = Annotated[
    Principal, Depends(auth.require(UserRole.ADMIN, UserRole.CUSTOMER_SUPPORT))
]
AdminPrincipal = Annotated[Principal, Depends(auth.require(UserRole.ADMIN))]
