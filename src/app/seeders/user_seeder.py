"""Dummy accounts covering every role in the access model."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants.enums import UserRole, UserStatus
from app.core.config import settings
from app.core.security import hash_password
from app.models.user import User
from app.models.user_warehouse_assignment import UserWarehouseAssignment
from app.seeders.base import Seeder, seed_id


@dataclass(frozen=True)
class SeedUser:
    email: str
    full_name: str
    role: UserRole
    phone: str | None = None
    status: UserStatus = UserStatus.ACTIVE
    #: Warehouse codes this operator may act on.
    warehouse_codes: tuple[str, ...] = ()


SEED_USERS: tuple[SeedUser, ...] = (
    SeedUser("admin@transfleet.com", "Sara Ahmed", UserRole.ADMIN, "+923001110001"),
    SeedUser(
        "support.lead@transfleet.com", "Bilal Raza", UserRole.CUSTOMER_SUPPORT, "+923001110002"
    ),
    SeedUser(
        "support.agent@transfleet.com", "Hina Malik", UserRole.CUSTOMER_SUPPORT, "+923001110003"
    ),
    SeedUser(
        "wh.karachi@transfleet.com",
        "Imran Sheikh",
        UserRole.WAREHOUSE_OPERATOR,
        "+923001110004",
        warehouse_codes=("KHI-01",),
    ),
    SeedUser(
        "wh.lahore@transfleet.com",
        "Nadia Iqbal",
        UserRole.WAREHOUSE_OPERATOR,
        "+923001110005",
        warehouse_codes=("LHE-01",),
    ),
    SeedUser(
        "wh.regional@transfleet.com",
        "Usman Tariq",
        UserRole.WAREHOUSE_OPERATOR,
        "+923001110006",
        warehouse_codes=("KHI-01", "LHE-01", "ISB-01"),
    ),
    SeedUser("courier.one@transfleet.com", "Ali Hassan", UserRole.COURIER, "+923001110007"),
    SeedUser("courier.two@transfleet.com", "Fatima Noor", UserRole.COURIER, "+923001110008"),
    SeedUser("courier.three@transfleet.com", "Zain Abbas", UserRole.COURIER, "+923001110009"),
    SeedUser("courier.four@transfleet.com", "Mehwish Khan", UserRole.COURIER, "+923001110010"),
    SeedUser(
        "courier.suspended@transfleet.com",
        "Rehan Aslam",
        UserRole.COURIER,
        "+923001110011",
        status=UserStatus.SUSPENDED,
    ),
)


class UserSeeder(Seeder):
    name = "users"

    async def run(self, session: AsyncSession) -> int:
        existing = set(
            (
                await session.scalars(
                    select(User.email).where(User.email.in_([u.email for u in SEED_USERS]))
                )
            ).all()
        )
        # Hash once: Argon2 is deliberately slow and every seeded account
        # shares the same development password.
        password_hash = hash_password(settings.seed_default_password)

        created = 0
        for spec in SEED_USERS:
            if spec.email in existing:
                continue

            user = User(
                id=seed_id("user", spec.email),
                email=spec.email,
                full_name=spec.full_name,
                phone=spec.phone,
                password_hash=password_hash,
                role=spec.role,
                status=spec.status,
            )
            session.add(user)
            for code in spec.warehouse_codes:
                session.add(
                    UserWarehouseAssignment(
                        id=seed_id("user_warehouse", f"{spec.email}:{code}"),
                        user_id=user.id,
                        # Matches the id the warehouse seeder will use for this code.
                        warehouse_id=seed_id("warehouse", code),
                    )
                )
            created += 1

        await session.flush()
        return created
