from typing import Annotated

from fastapi import Depends, Request

from identity.config import settings
from identity.db import SessionDep
from identity.keys import SigningKey
from identity.models import User
from identity.repository import UserRepository
from identity.services.auth import AuthService
from identity.services.users import UserService
from sl_platform.auth import Auth, Principal, TokenVerifier
from sl_platform.errors import AuthenticationError
from sl_platform.http import ServiceClient
from sl_platform.roles import UserRole

signing_key = SigningKey.from_settings(settings)

# Identity verifies its own tokens with its own public key — no JWKS round trip.
auth = Auth(
    TokenVerifier(
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        public_key_pem=signing_key.public_pem,
    )
)

warehouse_client = ServiceClient("warehouse", settings.warehouse_service_url)


def get_warehouse_client() -> ServiceClient:
    return warehouse_client


async def get_auth_service(session: SessionDep) -> AuthService:
    return AuthService(session, settings, signing_key)


async def get_user_service(
    session: SessionDep, warehouses: Annotated[ServiceClient, Depends(get_warehouse_client)]
) -> UserService:
    return UserService(session, warehouses)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
UserServiceDep = Annotated[UserService, Depends(get_user_service)]
CurrentPrincipal = Annotated[Principal, Depends(auth)]
AdminPrincipal = Annotated[Principal, Depends(auth.require(UserRole.ADMIN))]


async def get_current_user(principal: CurrentPrincipal, session: SessionDep) -> User:
    """Identity is the one service that may check the account itself on each
    request, so a suspended user is refused here immediately."""
    user = await UserRepository(session).get(principal.user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("Account is not active.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def client_info(request: Request) -> tuple[str | None, str | None]:
    """User agent and client IP. The edge proxy is trusted to set X-Forwarded-For."""
    forwarded = request.headers.get("x-forwarded-for")
    ip = (
        forwarded.split(",")[0].strip()
        if forwarded
        else (request.client.host if request.client else None)
    )
    return request.headers.get("user-agent"), ip


ClientInfo = Annotated[tuple[str | None, str | None], Depends(client_info)]
