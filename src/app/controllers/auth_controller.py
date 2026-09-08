from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.controllers.dependencies import AuthServiceDep, CurrentUser, client_info
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    RefreshRequest,
    SessionRead,
    TokenPair,
)
from app.schemas.common import MessageResponse, Problem
from app.schemas.user import UserRead

router = APIRouter(prefix="/auth", tags=["Authentication"])

ClientInfo = Annotated[tuple[str | None, str | None], Depends(client_info)]


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="Authenticate and receive a token pair",
    responses={
        401: {"model": Problem, "description": "Bad credentials, locked or inactive account"},
    },
)
async def login(
    payload: LoginRequest,
    auth: AuthServiceDep,
    client: ClientInfo,
) -> LoginResponse:
    """Repeated failures lock the account temporarily."""
    user_agent, ip_address = client
    user, tokens = await auth.login(
        payload.email, payload.password, user_agent=user_agent, ip_address=ip_address
    )
    return LoginResponse(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_in=tokens.expires_in,
        user=UserRead.model_validate(user),
    )


@router.post(
    "/refresh",
    response_model=TokenPair,
    summary="Exchange a refresh token for a new pair",
    responses={401: {"model": Problem, "description": "Token invalid, expired or revoked"}},
)
async def refresh(
    payload: RefreshRequest,
    auth: AuthServiceDep,
    client: ClientInfo,
) -> TokenPair:
    """Refresh tokens rotate: the presented token is revoked and replaced.

    Replaying an already-rotated token revokes every session for that user, on
    the assumption it was stolen.
    """
    user_agent, ip_address = client
    _, tokens = await auth.refresh(
        payload.refresh_token, user_agent=user_agent, ip_address=ip_address
    )
    return TokenPair(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_in=tokens.expires_in,
    )


@router.post(
    "/logout",
    response_model=MessageResponse,
    summary="Revoke the current session, or all sessions",
    responses={401: {"model": Problem, "description": "Refresh token invalid"}},
)
async def logout(
    payload: LogoutRequest,
    current_user: CurrentUser,
    auth: AuthServiceDep,
) -> MessageResponse:
    """Omit ``refresh_token`` to sign out of every device."""
    if payload.refresh_token is None:
        revoked = await auth.logout_all(current_user)
        return MessageResponse(message=f"Signed out of {revoked} session(s).")

    await auth.logout(payload.refresh_token, user=current_user)
    return MessageResponse(message="Signed out.")


@router.get(
    "/sessions",
    response_model=list[SessionRead],
    summary="List the caller's active sessions",
)
async def list_sessions(current_user: CurrentUser, auth: AuthServiceDep) -> list[SessionRead]:
    sessions = await auth.list_sessions(current_user)
    return [SessionRead.model_validate(s) for s in sessions]


@router.get(
    "/me",
    response_model=UserRead,
    status_code=status.HTTP_200_OK,
    summary="Return the authenticated user's profile",
)
async def me(current_user: CurrentUser) -> UserRead:
    return UserRead.model_validate(current_user)
