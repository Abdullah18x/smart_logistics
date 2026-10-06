from __future__ import annotations

import uuid

from identity.models import User, UserStatus, UserWarehouseAssignment
from identity.security import hash_password
from sl_platform.errors import NotFoundError
from sl_platform.roles import UserRole
from sl_platform.testing import mint_token

PASSWORD = "Correct-Horse-9!"
_HASH = hash_password(PASSWORD)


class FakeWarehouseClient:
    """Stands in for the Warehouse service: knows a set of warehouse ids."""

    def __init__(self) -> None:
        self.known: set[uuid.UUID] = set()
        self.calls: list[str] = []

    async def get(self, path: str, **_):
        self.calls.append(path)
        warehouse_id = uuid.UUID(path.rsplit("/", 1)[-1])
        if warehouse_id not in self.known:
            raise NotFoundError("Warehouse not found.")
        return None


class Factory:
    def __init__(self, sessionmaker, private_pem: str) -> None:
        self.sessionmaker = sessionmaker
        self.private_pem = private_pem

    async def user(
        self,
        role: UserRole = UserRole.ADMIN,
        *,
        email: str | None = None,
        status: UserStatus = UserStatus.ACTIVE,
        warehouse_ids: tuple[uuid.UUID, ...] = (),
    ) -> User:
        async with self.sessionmaker() as session:
            user = User(
                email=email or f"{uuid.uuid4().hex[:10]}@transfleet.com",
                full_name="Test User",
                password_hash=_HASH,
                role=role,
                status=status,
            )
            session.add(user)
            await session.flush()
            for warehouse_id in warehouse_ids:
                session.add(UserWarehouseAssignment(user_id=user.id, warehouse_id=warehouse_id))
            await session.commit()
            return user

    def headers(self, user: User) -> dict[str, str]:
        token = mint_token(self.private_pem, user_id=user.id, role=user.role)
        return {"Authorization": f"Bearer {token}"}
