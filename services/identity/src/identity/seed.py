"""Development accounts: ``python -m identity.seed``.

Ids are deterministic (uuid5), and warehouse ids use the same derivation as the
Warehouse service's seeder, so operator scopes line up across databases with
no shared table.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select

from identity.config import settings
from identity.db import database
from identity.models import User, UserStatus, UserWarehouseAssignment
from identity.security import hash_password
from sl_platform.roles import UserRole

logger = logging.getLogger("identity.seed")
SEED_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")


def seed_id(entity: str, key: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, f"{entity}:{key}")


@dataclass(frozen=True)
class SeedUser:
    email: str
    full_name: str
    role: UserRole
    phone: str
    status: UserStatus = UserStatus.ACTIVE
    warehouse_codes: tuple[str, ...] = ()
    #: Courier fleet profile code (owned by the Courier service, Phase 2).
    courier_code: str | None = None


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
    SeedUser(
        "courier.one@transfleet.com",
        "Ali Hassan",
        UserRole.COURIER,
        "+923001110007",
        courier_code="CR-0001",
    ),
    SeedUser(
        "courier.two@transfleet.com",
        "Fatima Noor",
        UserRole.COURIER,
        "+923001110008",
        courier_code="CR-0002",
    ),
    SeedUser(
        "courier.three@transfleet.com",
        "Zain Abbas",
        UserRole.COURIER,
        "+923001110009",
        courier_code="CR-0003",
    ),
    SeedUser(
        "courier.four@transfleet.com",
        "Mehwish Khan",
        UserRole.COURIER,
        "+923001110010",
        courier_code="CR-0004",
    ),
    SeedUser(
        "courier.suspended@transfleet.com",
        "Rehan Aslam",
        UserRole.COURIER,
        "+923001110011",
        status=UserStatus.SUSPENDED,
        courier_code="CR-0005",
    ),
)


async def seed() -> int:
    if settings.is_production:
        raise RuntimeError("Refusing to seed a production database.")
    async with database.scope() as session:
        existing = set(
            (
                await session.scalars(
                    select(User.email).where(User.email.in_([u.email for u in SEED_USERS]))
                )
            ).all()
        )
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
                courier_id=seed_id("courier", spec.courier_code) if spec.courier_code else None,
            )
            session.add(user)
            for code in spec.warehouse_codes:
                session.add(
                    UserWarehouseAssignment(
                        id=seed_id("user_warehouse", f"{spec.email}:{code}"),
                        user_id=user.id,
                        warehouse_id=seed_id("warehouse", code),
                    )
                )
            created += 1
    return created


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
    created = asyncio.run(seed())
    logger.info(
        "identity: %d user(s) created (password: %s)", created, settings.seed_default_password
    )


if __name__ == "__main__":
    main()
