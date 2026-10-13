from typing import Annotated

from fastapi import Depends

from courier.config import settings
from courier.db import SessionDep
from courier.services.assignments import AssignmentService
from courier.services.couriers import CourierService
from sl_platform.auth import Auth, Principal, TokenVerifier
from sl_platform.roles import UserRole

verifier = TokenVerifier.from_settings(settings)
auth = Auth(verifier)


async def get_couriers(session: SessionDep) -> CourierService:
    return CourierService(session)


async def get_assignments(session: SessionDep) -> AssignmentService:
    return AssignmentService(session)


CouriersDep = Annotated[CourierService, Depends(get_couriers)]
AssignmentsDep = Annotated[AssignmentService, Depends(get_assignments)]

AnyPrincipal = Annotated[Principal, Depends(auth)]
AdminPrincipal = Annotated[Principal, Depends(auth.require(UserRole.ADMIN))]
CourierPrincipal = Annotated[Principal, Depends(auth.require(UserRole.COURIER))]
StaffPrincipal = Annotated[
    Principal,
    Depends(
        auth.require(
            UserRole.ADMIN,
            UserRole.CUSTOMER_SUPPORT,
            UserRole.WAREHOUSE_OPERATOR,
            UserRole.SYSTEM,
        )
    ),
]
#: The dispatch workflow assigns and releases; an admin may do it by hand.
DispatchPrincipal = Annotated[Principal, Depends(auth.require(UserRole.SYSTEM, UserRole.ADMIN))]
#: The manual override: operations staff hand a shipment to another courier.
#: The delivery workflow does too, when a pickup is overdue (escalation).
ReassignPrincipal = Annotated[
    Principal,
    Depends(auth.require(UserRole.ADMIN, UserRole.CUSTOMER_SUPPORT, UserRole.SYSTEM)),
]
