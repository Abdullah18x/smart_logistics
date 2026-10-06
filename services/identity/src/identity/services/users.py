"""User management. Every change emits an event through the outbox."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from identity.events import UserEvents, emit
from identity.models import User, UserStatus, UserWarehouseAssignment
from identity.repository import AssignmentRepository, RefreshTokenRepository, UserRepository
from identity.schemas import UserCreate, UserRoleUpdate, UserStatusUpdate, UserUpdate
from identity.security import hash_password, verify_password
from sl_platform.db import utcnow
from sl_platform.errors import ConflictError, NotFoundError, PermissionDeniedError
from sl_platform.http import ServiceClient
from sl_platform.roles import UserRole


class UserService:
    def __init__(self, session: AsyncSession, warehouses: ServiceClient | None = None) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.tokens = RefreshTokenRepository(session)
        self.assignments = AssignmentRepository(session)
        self.warehouses = warehouses

    async def get(self, user_id: uuid.UUID) -> User:
        user = await self.users.get(user_id)
        if user is None:
            raise NotFoundError("User not found.")
        return user

    async def list(self, **filters) -> tuple[list[User], int]:
        return await self.users.list_users(**filters)

    async def create(self, payload: UserCreate) -> User:
        # The unique index on email is the authority; a pre-check would race.
        user = User(
            email=payload.email,
            full_name=payload.full_name,
            phone=payload.phone,
            password_hash=hash_password(payload.password),
            password_changed_at=utcnow(),
            role=payload.role,
            status=UserStatus.ACTIVE,
        )
        self.session.add(user)
        await self.session.flush()
        emit(self.session, UserEvents.CREATED, user)
        return user

    async def update_profile(self, user_id: uuid.UUID, payload: UserUpdate) -> User:
        user = await self.get(user_id)
        changes = payload.model_dump(exclude_unset=True)
        if "full_name" in changes and changes["full_name"] is None:
            raise ConflictError("full_name cannot be cleared.")
        for field, value in changes.items():
            setattr(user, field, value)
        await self.session.flush()
        emit(self.session, UserEvents.UPDATED, user)
        return user

    async def change_role(
        self, user_id: uuid.UUID, payload: UserRoleUpdate, *, actor_id: uuid.UUID
    ) -> User:
        user = await self.get(user_id)
        if user.id == actor_id:
            raise PermissionDeniedError("You cannot change your own role.")
        previous = user.role
        user.role = payload.role
        # The role rides in the token: revoke sessions so the next refresh
        # carries the new one. Existing access tokens expire within their TTL.
        await self.tokens.revoke_all(user.id)
        await self.session.flush()
        emit(
            self.session,
            UserEvents.ROLE_CHANGED,
            user,
            previous_role=previous.value,
            reason=payload.reason,
            actor_id=actor_id,
        )
        return user

    async def change_status(
        self, user_id: uuid.UUID, payload: UserStatusUpdate, *, actor_id: uuid.UUID
    ) -> User:
        user = await self.get(user_id)
        if user.id == actor_id:
            raise PermissionDeniedError("You cannot change your own status.")
        previous = user.status
        user.status = payload.status
        if payload.status is not UserStatus.ACTIVE:
            await self.tokens.revoke_all(user.id)
        await self.session.flush()
        emit(
            self.session,
            UserEvents.STATUS_CHANGED,
            user,
            previous_status=previous.value,
            reason=payload.reason,
            actor_id=actor_id,
        )
        return user

    async def change_password(self, user: User, current: str, new: str) -> None:
        if not verify_password(current, user.password_hash):
            raise PermissionDeniedError("Current password is incorrect.")
        if verify_password(new, user.password_hash):
            raise ConflictError("New password must differ from the current one.")
        user.password_hash = hash_password(new)
        user.password_changed_at = utcnow()
        await self.tokens.revoke_all(user.id)
        await self.session.flush()

    async def delete(self, user_id: uuid.UUID, *, actor_id: uuid.UUID) -> None:
        user = await self.get(user_id)
        if user.id == actor_id:
            raise PermissionDeniedError("You cannot delete your own account.")
        user.status = UserStatus.DEACTIVATED
        user.deleted_at = utcnow()
        user.deleted_by = actor_id
        await self.tokens.revoke_all(user.id)
        await self.session.flush()
        emit(self.session, UserEvents.DELETED, user, actor_id=actor_id)

    async def restore(self, user_id: uuid.UUID) -> User:
        user = await self.users.get(user_id, include_deleted=True)
        if user is None:
            raise NotFoundError("User not found.")
        user.deleted_at = None
        user.deleted_by = None
        user.status = UserStatus.ACTIVE
        await self.session.flush()
        emit(self.session, UserEvents.RESTORED, user)
        return user

    # --- warehouse scoping --------------------------------------------------------

    async def list_assignments(self, user_id: uuid.UUID) -> list[UserWarehouseAssignment]:
        await self.get(user_id)
        return await self.assignments.list_for_user(user_id)

    async def assign_warehouse(
        self, user_id: uuid.UUID, warehouse_id: uuid.UUID, *, token: str
    ) -> UserWarehouseAssignment:
        user = await self.get(user_id)
        if user.role is not UserRole.WAREHOUSE_OPERATOR:
            raise ConflictError("Only warehouse operators are scoped to warehouses.")
        # No foreign key can check this any more: ask the owner. Raises the
        # Warehouse service's own 404 if the id is unknown.
        if self.warehouses is not None:
            await self.warehouses.get(f"/api/v1/warehouses/{warehouse_id}", token=token)
        assignment = UserWarehouseAssignment(user_id=user.id, warehouse_id=warehouse_id)
        self.session.add(assignment)
        await self.session.flush()
        await self.tokens.revoke_all(user.id)
        emit(self.session, UserEvents.SCOPE_CHANGED, user, added_warehouse_id=warehouse_id)
        return assignment

    async def unassign_warehouse(self, user_id: uuid.UUID, warehouse_id: uuid.UUID) -> None:
        user = await self.get(user_id)
        assignment = await self.assignments.get(user_id, warehouse_id)
        if assignment is None:
            raise NotFoundError("Assignment not found.")
        await self.session.delete(assignment)
        await self.tokens.revoke_all(user.id)
        await self.session.flush()
        emit(self.session, UserEvents.SCOPE_CHANGED, user, removed_warehouse_id=warehouse_id)
