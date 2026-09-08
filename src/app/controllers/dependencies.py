import uuid
from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants.enums import UserRole
from app.core.access import AccessScope
from app.core.database import get_db_session
from app.core.exceptions import AuthenticationError, PermissionDeniedError
from app.models.user import User
from app.repositories.courier_repository import CourierRepository
from app.repositories.user_repository import UserRepository
from app.repositories.warehouse_repository import UserWarehouseScopeRepository
from app.services.auth_service import AuthService
from app.services.idempotency_service import IdempotencyService
from app.services.inventory_service import InventoryService
from app.services.shipment_service import ShipmentService
from app.services.user_service import UserService
from app.services.warehouse_service import WarehouseService

bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token")

SessionDep = Annotated[AsyncSession, Depends(get_db_session)]


async def get_user_service(session: SessionDep) -> UserService:
    return UserService(session)


async def get_auth_service(session: SessionDep) -> AuthService:
    return AuthService(session)


async def get_warehouse_service(session: SessionDep) -> WarehouseService:
    return WarehouseService(session)


async def get_shipment_service(session: SessionDep) -> ShipmentService:
    return ShipmentService(session)


async def get_inventory_service(session: SessionDep) -> InventoryService:
    return InventoryService(session)


async def get_idempotency_service(session: SessionDep) -> IdempotencyService:
    return IdempotencyService(session)


UserServiceDep = Annotated[UserService, Depends(get_user_service)]
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
WarehouseServiceDep = Annotated[WarehouseService, Depends(get_warehouse_service)]
ShipmentServiceDep = Annotated[ShipmentService, Depends(get_shipment_service)]
InventoryServiceDep = Annotated[InventoryService, Depends(get_inventory_service)]
IdempotencyServiceDep = Annotated[IdempotencyService, Depends(get_idempotency_service)]


async def get_current_user(
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User:
    """Resolve the caller from the bearer token."""
    # Imported here to keep the token module free of FastAPI imports.
    from app.core.tokens import decode_access_token

    if credentials is None:
        raise AuthenticationError("Authorization header is missing.")

    payload = decode_access_token(credentials.credentials)
    user = await UserRepository(session).get(uuid.UUID(payload["sub"]))
    if user is None:
        raise AuthenticationError("Token subject no longer exists.")
    if not user.is_active:
        # Catches accounts suspended after the token was issued.
        raise AuthenticationError("Account is not active.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: UserRole) -> Callable[..., Coroutine[Any, Any, User]]:
    """Role gate. Row-level scoping is applied separately, in repositories."""

    async def dependency(user: CurrentUser) -> User:
        if user.role not in roles:
            raise PermissionDeniedError(
                "This action requires one of: " + ", ".join(r.value for r in roles)
            )
        return user

    return dependency


require_admin = require_roles(UserRole.ADMIN)
AdminUser = Annotated[User, Depends(require_admin)]


async def get_access_scope(user: CurrentUser, session: SessionDep) -> AccessScope:
    """Builds the caller's row-level scope once per request.

    Admins and support staff are unrestricted. A warehouse operator is limited
    to their assigned warehouses, a courier to their own assignments.
    """
    if user.role is UserRole.WAREHOUSE_OPERATOR:
        warehouse_ids = await UserWarehouseScopeRepository(session).warehouse_ids_for(user.id)
        return AccessScope(role=user.role, user_id=user.id, warehouse_ids=warehouse_ids)

    if user.role is UserRole.COURIER:
        courier = await CourierRepository(session).get_by_user_id(user.id)
        return AccessScope(
            role=user.role,
            user_id=user.id,
            # A courier with no fleet profile can see no shipments, which is
            # correct: nothing has been assigned to them.
            courier_id=courier.id if courier else uuid.UUID(int=0),
        )

    return AccessScope(role=user.role, user_id=user.id)


Scope = Annotated[AccessScope, Depends(get_access_scope)]


def client_info(request: Request) -> tuple[str | None, str | None]:
    """User agent and client IP, recorded against each session."""
    user_agent = request.headers.get("user-agent")
    forwarded = request.headers.get("x-forwarded-for")
    ip = (
        forwarded.split(",")[0].strip()
        if forwarded
        else (request.client.host if request.client else None)
    )
    return user_agent, ip
