"""Development fleet: ``python -m courier.seed``.

Ids are deterministic (uuid5) with the same derivation Identity's seeder uses
for ``users.courier_id`` and Warehouse's for warehouse ids, so the profile,
the login and the home warehouse line up across three databases.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select

from courier.config import settings
from courier.db import database
from courier.events import CourierEvents, emit
from courier.models import Availability, Courier, CourierStatus, VehicleType

logger = logging.getLogger("courier.seed")
SEED_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")


def seed_id(entity: str, key: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, f"{entity}:{key}")


@dataclass(frozen=True)
class SeedCourier:
    code: str
    full_name: str
    phone: str
    vehicle: VehicleType
    capacity: int
    city: str
    warehouse_code: str | None
    email: str | None = None
    availability: Availability = Availability.AVAILABLE
    status: CourierStatus = CourierStatus.ACTIVE
    max_weight_g: int | None = None


SEED_COURIERS = (
    SeedCourier(
        "CR-0001", "Ali Hassan", "+923001110007", VehicleType.MOTORCYCLE, 8, "Karachi",
        "KHI-01", email="courier.one@transfleet.com", max_weight_g=30_000,
    ),
    SeedCourier(
        "CR-0002", "Fatima Noor", "+923001110008", VehicleType.CAR, 12, "Lahore",
        "LHE-01", email="courier.two@transfleet.com", max_weight_g=150_000,
    ),
    SeedCourier(
        "CR-0003", "Zain Abbas", "+923001110009", VehicleType.VAN, 20, "Karachi",
        "KHI-01", email="courier.three@transfleet.com", max_weight_g=800_000,
    ),
    SeedCourier(
        "CR-0004", "Mehwish Khan", "+923001110010", VehicleType.MOTORCYCLE, 8, "Islamabad",
        "ISB-01", email="courier.four@transfleet.com", availability=Availability.OFFLINE,
        max_weight_g=30_000,
    ),
    SeedCourier(
        "CR-0005", "Rehan Aslam", "+923001110011", VehicleType.MOTORCYCLE, 8, "Lahore",
        "LHE-01", email="courier.suspended@transfleet.com",
        availability=Availability.OFFLINE, status=CourierStatus.SUSPENDED,
    ),
    # A partner without a login: assignable, but nobody signs in as them.
    SeedCourier(
        "CR-0006", "Karachi Express Partners", "+922134567890", VehicleType.TRUCK, 40,
        "Karachi", None, max_weight_g=5_000_000,
    ),
)  # fmt: skip


async def seed() -> int:
    if settings.is_production:
        raise RuntimeError("Refusing to seed a production database.")
    async with database.scope() as session:
        codes = [c.code for c in SEED_COURIERS]
        existing = set(
            (await session.scalars(select(Courier.code).where(Courier.code.in_(codes)))).all()
        )
        created = 0
        for spec in SEED_COURIERS:
            if spec.code in existing:
                continue
            courier = Courier(
                id=seed_id("courier", spec.code),
                code=spec.code,
                user_id=seed_id("user", spec.email) if spec.email else None,
                full_name=spec.full_name,
                phone=spec.phone,
                email=spec.email,
                vehicle_type=spec.vehicle,
                max_active_shipments=spec.capacity,
                max_weight_g=spec.max_weight_g,
                home_city=spec.city,
                home_warehouse_id=(
                    seed_id("warehouse", spec.warehouse_code) if spec.warehouse_code else None
                ),
                status=spec.status,
                availability=spec.availability,
            )
            session.add(courier)
            await session.flush()
            emit(session, CourierEvents.CREATED, courier)
            created += 1
    return created


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
    logger.info("courier: %d courier(s) created", asyncio.run(seed()))


if __name__ == "__main__":
    main()
