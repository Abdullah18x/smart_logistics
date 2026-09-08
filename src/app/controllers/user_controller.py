import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.constants.enums import UserRole, UserStatus
from app.controllers.dependencies import CurrentUser, UserServiceDep, require_admin
from app.models.user import User
from app.schemas.common import MessageResponse, Page, Problem
from app.schemas.user import (
    PasswordChange,
    UserCreate,
    UserRead,
    UserRoleUpdate,
    UserStatusUpdate,
    UserUpdate,
)

router = APIRouter(prefix="/users", tags=["Users"])

AdminOnly = Annotated[User, Depends(require_admin)]


@router.post(
    "",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a user",
    responses={
        403: {"model": Problem, "description": "Admin role required"},
        409: {"model": Problem, "description": "Email already registered"},
    },
)
async def create_user(
    payload: UserCreate,
    _admin: AdminOnly,
    users: UserServiceDep,
) -> UserRead:
    """Accounts are created by an admin; there is no public self-registration."""
    user = await users.create(payload)
    return UserRead.model_validate(user)


@router.get(
    "",
    response_model=Page[UserRead],
    summary="List users",
    responses={403: {"model": Problem, "description": "Admin role required"}},
)
async def list_users(
    _admin: AdminOnly,
    users: UserServiceDep,
    role: Annotated[UserRole | None, Query(description="Filter by role.")] = None,
    account_status: Annotated[
        UserStatus | None, Query(alias="status", description="Filter by account status.")
    ] = None,
    search: Annotated[
        str | None, Query(max_length=200, description="Matches name or email.")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[UserRead]:
    records, total = await users.list(
        role=role, status=account_status, search=search, limit=limit, offset=offset
    )
    return Page[UserRead](
        items=[UserRead.model_validate(u) for u in records],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{user_id}",
    response_model=UserRead,
    summary="Fetch a user",
    responses={
        403: {"model": Problem, "description": "Admin role required"},
        404: {"model": Problem, "description": "User not found"},
    },
)
async def get_user(
    user_id: uuid.UUID,
    _admin: AdminOnly,
    users: UserServiceDep,
) -> UserRead:
    return UserRead.model_validate(await users.get(user_id))


@router.patch(
    "/me",
    response_model=UserRead,
    summary="Update the caller's own profile",
)
async def update_own_profile(
    payload: UserUpdate,
    current_user: CurrentUser,
    users: UserServiceDep,
) -> UserRead:
    """Role and status are deliberately not editable here."""
    user = await users.update_profile(current_user.id, payload)
    return UserRead.model_validate(user)


@router.post(
    "/me/password",
    response_model=MessageResponse,
    summary="Change the caller's password",
    responses={
        403: {"model": Problem, "description": "Current password is incorrect"},
        409: {"model": Problem, "description": "New password matches the old one"},
    },
)
async def change_own_password(
    payload: PasswordChange,
    current_user: CurrentUser,
    users: UserServiceDep,
) -> MessageResponse:
    """All other sessions are revoked on success."""
    await users.change_password(current_user, payload.current_password, payload.new_password)
    return MessageResponse(message="Password changed. Other sessions have been signed out.")


@router.patch(
    "/{user_id}",
    response_model=UserRead,
    summary="Update a user's profile",
    responses={
        403: {"model": Problem, "description": "Admin role required"},
        404: {"model": Problem, "description": "User not found"},
    },
)
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    _admin: AdminOnly,
    users: UserServiceDep,
) -> UserRead:
    return UserRead.model_validate(await users.update_profile(user_id, payload))


@router.patch(
    "/{user_id}/role",
    response_model=UserRead,
    summary="Change a user's role",
    responses={
        403: {"model": Problem, "description": "Admin role required, or self-modification"},
        404: {"model": Problem, "description": "User not found"},
    },
)
async def change_user_role(
    user_id: uuid.UUID,
    payload: UserRoleUpdate,
    admin: AdminOnly,
    users: UserServiceDep,
) -> UserRead:
    return UserRead.model_validate(await users.change_role(user_id, payload, actor=admin))


@router.patch(
    "/{user_id}/status",
    response_model=UserRead,
    summary="Suspend, reactivate or deactivate a user",
    responses={
        403: {"model": Problem, "description": "Admin role required, or self-modification"},
        404: {"model": Problem, "description": "User not found"},
    },
)
async def change_user_status(
    user_id: uuid.UUID,
    payload: UserStatusUpdate,
    admin: AdminOnly,
    users: UserServiceDep,
) -> UserRead:
    """Deactivating or suspending an account revokes all of its sessions."""
    return UserRead.model_validate(await users.change_status(user_id, payload, actor=admin))


@router.post(
    "/{user_id}/restore",
    response_model=UserRead,
    summary="Restore a soft-deleted user",
    responses={
        403: {"model": Problem, "description": "Admin role required"},
        404: {"model": Problem, "description": "User not found"},
    },
)
async def restore_user(
    user_id: uuid.UUID,
    _admin: AdminOnly,
    users: UserServiceDep,
) -> UserRead:
    return UserRead.model_validate(await users.restore(user_id))


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Deactivate a user",
    responses={
        403: {"model": Problem, "description": "Admin role required, or self-deletion"},
        404: {"model": Problem, "description": "User not found"},
    },
)
async def delete_user(
    user_id: uuid.UUID,
    admin: AdminOnly,
    users: UserServiceDep,
) -> None:
    """Soft delete: other records reference users by id, so the row is kept and
    the account is marked deactivated."""
    await users.delete(user_id, actor=admin)
