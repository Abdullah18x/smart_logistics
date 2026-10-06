import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from identity.api.deps import AdminPrincipal, CurrentUser, UserServiceDep
from identity.db import SessionDep, idempotency
from identity.models import UserStatus
from identity.schemas import (
    PasswordChange,
    UserCreate,
    UserRead,
    UserRoleUpdate,
    UserStatusUpdate,
    UserUpdate,
    WarehouseAssignmentCreate,
    WarehouseAssignmentRead,
)
from sl_platform.idempotency import IdempotencyKeyHeader, run_idempotent
from sl_platform.roles import UserRole
from sl_platform.schemas import MessageResponse, Page, Problem

router = APIRouter(prefix="/api/v1/users", tags=["Users"])

FORBIDDEN = {"model": Problem, "description": "Admin role required"}
NOT_FOUND = {"model": Problem, "description": "User not found"}


@router.post(
    "",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    responses={403: FORBIDDEN, 409: {"model": Problem}},
    summary="Create a user",
)
async def create_user(
    payload: UserCreate,
    admin: AdminPrincipal,
    users: UserServiceDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyHeader = None,
):
    async def action() -> UserRead:
        return UserRead.model_validate(await users.create(payload))

    return await run_idempotent(
        idempotency,
        session,
        key=idempotency_key,
        owner=str(admin.user_id),
        endpoint="POST /api/v1/users",
        payload=payload.model_dump(mode="json"),
        action=action,
        status_code=201,
    )


@router.get("", response_model=Page[UserRead], responses={403: FORBIDDEN}, summary="List users")
async def list_users(
    _admin: AdminPrincipal,
    users: UserServiceDep,
    role: Annotated[UserRole | None, Query()] = None,
    account_status: Annotated[UserStatus | None, Query(alias="status")] = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[UserRead]:
    records, total = await users.list(
        role=role, status=account_status, search=search, limit=limit, offset=offset
    )
    return Page[UserRead](
        items=[UserRead.model_validate(u) for u in records], total=total, limit=limit, offset=offset
    )


@router.patch("/me", response_model=UserRead, summary="Update the caller's own profile")
async def update_me(
    payload: UserUpdate, user: CurrentUser, users: UserServiceDep, session: SessionDep
) -> UserRead:
    result = UserRead.model_validate(await users.update_profile(user.id, payload))
    await session.commit()
    return result


@router.post(
    "/me/password",
    response_model=MessageResponse,
    responses={403: {"model": Problem}, 409: {"model": Problem}},
    summary="Change the caller's password",
)
async def change_password(
    payload: PasswordChange, user: CurrentUser, users: UserServiceDep, session: SessionDep
) -> MessageResponse:
    await users.change_password(user, payload.current_password, payload.new_password)
    await session.commit()
    return MessageResponse(message="Password changed. Other sessions have been signed out.")


@router.get(
    "/{user_id}",
    response_model=UserRead,
    responses={403: FORBIDDEN, 404: NOT_FOUND},
    summary="Fetch a user",
)
async def get_user(user_id: uuid.UUID, _admin: AdminPrincipal, users: UserServiceDep) -> UserRead:
    return UserRead.model_validate(await users.get(user_id))


@router.patch(
    "/{user_id}",
    response_model=UserRead,
    responses={403: FORBIDDEN, 404: NOT_FOUND},
    summary="Update a user",
)
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    _admin: AdminPrincipal,
    users: UserServiceDep,
    session: SessionDep,
) -> UserRead:
    result = UserRead.model_validate(await users.update_profile(user_id, payload))
    await session.commit()
    return result


@router.patch(
    "/{user_id}/role",
    response_model=UserRead,
    responses={403: FORBIDDEN, 404: NOT_FOUND},
    summary="Change role",
)
async def change_role(
    user_id: uuid.UUID,
    payload: UserRoleUpdate,
    admin: AdminPrincipal,
    users: UserServiceDep,
    session: SessionDep,
) -> UserRead:
    result = UserRead.model_validate(
        await users.change_role(user_id, payload, actor_id=admin.user_id)
    )
    await session.commit()
    return result


@router.patch(
    "/{user_id}/status",
    response_model=UserRead,
    responses={403: FORBIDDEN, 404: NOT_FOUND},
    summary="Suspend, reactivate or deactivate",
)
async def change_status(
    user_id: uuid.UUID,
    payload: UserStatusUpdate,
    admin: AdminPrincipal,
    users: UserServiceDep,
    session: SessionDep,
) -> UserRead:
    result = UserRead.model_validate(
        await users.change_status(user_id, payload, actor_id=admin.user_id)
    )
    await session.commit()
    return result


@router.post(
    "/{user_id}/restore",
    response_model=UserRead,
    responses={403: FORBIDDEN, 404: NOT_FOUND},
    summary="Restore a soft-deleted user",
)
async def restore_user(
    user_id: uuid.UUID, _admin: AdminPrincipal, users: UserServiceDep, session: SessionDep
) -> UserRead:
    result = UserRead.model_validate(await users.restore(user_id))
    await session.commit()
    return result


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={403: FORBIDDEN, 404: NOT_FOUND},
    summary="Deactivate (soft delete) a user",
)
async def delete_user(
    user_id: uuid.UUID, admin: AdminPrincipal, users: UserServiceDep, session: SessionDep
) -> Response:
    await users.delete(user_id, actor_id=admin.user_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- warehouse scoping ---------------------------------------------------------


@router.get(
    "/{user_id}/warehouses",
    response_model=list[WarehouseAssignmentRead],
    responses={403: FORBIDDEN, 404: NOT_FOUND},
    summary="Warehouses an operator is scoped to",
)
async def list_assignments(
    user_id: uuid.UUID, _admin: AdminPrincipal, users: UserServiceDep
) -> list[WarehouseAssignmentRead]:
    return [
        WarehouseAssignmentRead.model_validate(a) for a in await users.list_assignments(user_id)
    ]


@router.post(
    "/{user_id}/warehouses",
    response_model=WarehouseAssignmentRead,
    status_code=status.HTTP_201_CREATED,
    responses={403: FORBIDDEN, 404: {"model": Problem}, 409: {"model": Problem}},
    summary="Scope an operator to a warehouse",
)
async def assign_warehouse(
    user_id: uuid.UUID,
    payload: WarehouseAssignmentCreate,
    admin: AdminPrincipal,
    users: UserServiceDep,
    session: SessionDep,
) -> WarehouseAssignmentRead:
    """The warehouse id is checked with the Warehouse service — there is no
    foreign key across databases. The operator's sessions are revoked so their
    next token carries the new scope."""
    assignment = await users.assign_warehouse(user_id, payload.warehouse_id, token=admin.token)
    result = WarehouseAssignmentRead.model_validate(assignment)
    await session.commit()
    return result


@router.delete(
    "/{user_id}/warehouses/{warehouse_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={403: FORBIDDEN, 404: {"model": Problem}},
    summary="Remove an operator's warehouse scope",
)
async def unassign_warehouse(
    user_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    _admin: AdminPrincipal,
    users: UserServiceDep,
    session: SessionDep,
) -> Response:
    await users.unassign_warehouse(user_id, warehouse_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
