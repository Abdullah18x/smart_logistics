from fastapi import APIRouter

from identity.api.deps import (
    AuthServiceDep,
    ClientInfo,
    CurrentPrincipal,
    CurrentUser,
    signing_key,
)
from identity.db import SessionDep
from identity.repository import RefreshTokenRepository
from identity.schemas import (
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    RefreshRequest,
    SessionRead,
    TokenPair,
    UserRead,
)
from sl_platform.schemas import MessageResponse, Problem

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])
jwks_router = APIRouter(tags=["Authentication"])


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="Authenticate and receive a token pair",
    responses={401: {"model": Problem}},
)
async def login(
    payload: LoginRequest, auth: AuthServiceDep, client: ClientInfo, session: SessionDep
) -> LoginResponse:
    """The access token carries role, warehouse scope and courier id, so every
    other service can authorise without calling Identity."""
    user_agent, ip = client
    user, tokens = await auth.login(
        payload.email, payload.password, user_agent=user_agent, ip_address=ip
    )
    response = LoginResponse(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_in=tokens.expires_in,
        user=UserRead.model_validate(user),
    )
    await session.commit()
    return response


@router.post(
    "/refresh",
    response_model=TokenPair,
    summary="Exchange a refresh token for a new pair",
    responses={401: {"model": Problem}},
)
async def refresh(
    payload: RefreshRequest, auth: AuthServiceDep, client: ClientInfo, session: SessionDep
) -> TokenPair:
    """Refresh tokens rotate. Replaying a rotated token revokes every session."""
    user_agent, ip = client
    tokens = await auth.refresh(payload.refresh_token, user_agent=user_agent, ip_address=ip)
    await session.commit()
    return TokenPair(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_in=tokens.expires_in,
    )


@router.post("/logout", response_model=MessageResponse, summary="Revoke one or all sessions")
async def logout(
    payload: LogoutRequest, principal: CurrentPrincipal, auth: AuthServiceDep, session: SessionDep
) -> MessageResponse:
    if payload.refresh_token is None:
        count = await auth.logout_all(principal.user_id)
        await session.commit()
        return MessageResponse(message=f"Signed out of {count} session(s).")
    await auth.logout(payload.refresh_token, user_id=principal.user_id)
    await session.commit()
    return MessageResponse(message="Signed out.")


@router.get("/sessions", response_model=list[SessionRead], summary="The caller's active sessions")
async def sessions(principal: CurrentPrincipal, session: SessionDep) -> list[SessionRead]:
    rows = await RefreshTokenRepository(session).list_active(principal.user_id)
    return [SessionRead.model_validate(r) for r in rows]


@router.get("/me", response_model=UserRead, summary="The caller's profile")
async def me(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)


@jwks_router.get("/.well-known/jwks.json", summary="Public signing keys (JWKS)")
async def jwks() -> dict:
    """Every service fetches this once and verifies tokens locally."""
    return signing_key.jwks()
