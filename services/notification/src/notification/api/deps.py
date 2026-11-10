from typing import Annotated

from fastapi import Depends

from notification.config import settings
from notification.db import SessionDep
from notification.services.notifications import NotificationService
from sl_platform.auth import Auth, Principal, TokenVerifier
from sl_platform.revocation import TokenDenylist
from sl_platform.roles import UserRole

verifier = TokenVerifier.from_settings(settings)
auth = Auth(verifier, TokenDenylist.from_settings(settings))


async def get_notifications(session: SessionDep) -> NotificationService:
    return NotificationService(session, settings)


NotificationsDep = Annotated[NotificationService, Depends(get_notifications)]

#: Who may read the delivery log: the people who answer "did they get told?".
StaffPrincipal = Annotated[
    Principal, Depends(auth.require(UserRole.ADMIN, UserRole.CUSTOMER_SUPPORT))
]
AdminPrincipal = Annotated[Principal, Depends(auth.require(UserRole.ADMIN))]
CourierPrincipal = Annotated[Principal, Depends(auth.require(UserRole.COURIER))]
