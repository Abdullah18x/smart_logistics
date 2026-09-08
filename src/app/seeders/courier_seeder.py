"""Courier fleet profiles, linked to the seeded courier logins."""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import CourierAvailability, VehicleType
from app.models.courier import Courier
from app.seeders.base import Seeder, seed_id


@dataclass(frozen=True)
class SeedCourier:
    employee_code: str
    user_email: str | None
    full_name: str
    phone: str
    vehicle_type: VehicleType
    capacity_kg: str
    home_city: str
    base_warehouse_code: str
    availability: CourierAvailability
    max_daily_shipments: int
    rating: str
    total_deliveries: int
    failed_deliveries: int
    current_load: int = 0
    service_cities: tuple[str, ...] = ()
    can_handle_hazmat: bool = False
    can_handle_cold_chain: bool = False
    is_active: bool = True


SEED_COURIERS: tuple[SeedCourier, ...] = (
    SeedCourier(
        "CR-0001",
        "courier.one@transfleet.com",
        "Ali Hassan",
        "+923001110007",
        VehicleType.VAN,
        "800.00",
        "Karachi",
        "KHI-01",
        CourierAvailability.AVAILABLE,
        30,
        "4.70",
        412,
        11,
        current_load=6,
        service_cities=("Hyderabad",),
    ),
    SeedCourier(
        "CR-0002",
        "courier.two@transfleet.com",
        "Fatima Noor",
        "+923001110008",
        VehicleType.CAR,
        "300.00",
        "Lahore",
        "LHE-01",
        CourierAvailability.ON_DUTY,
        25,
        "4.90",
        508,
        6,
        current_load=14,
        can_handle_cold_chain=True,
    ),
    SeedCourier(
        "CR-0003",
        "courier.three@transfleet.com",
        "Zain Abbas",
        "+923001110009",
        VehicleType.MOTORCYCLE,
        "40.00",
        "Islamabad",
        "ISB-01",
        CourierAvailability.ON_DUTY,
        18,
        "4.30",
        233,
        19,
        current_load=9,
        service_cities=("Rawalpindi",),
    ),
    SeedCourier(
        "CR-0004",
        "courier.four@transfleet.com",
        "Mehwish Khan",
        "+923001110010",
        VehicleType.TRUCK,
        "3500.00",
        "Karachi",
        "KHI-01",
        CourierAvailability.OFF_DUTY,
        12,
        "4.60",
        178,
        4,
        can_handle_hazmat=True,
    ),
    # Suspended login; the fleet profile is deactivated to match.
    SeedCourier(
        "CR-0005",
        "courier.suspended@transfleet.com",
        "Rehan Aslam",
        "+923001110011",
        VehicleType.MOTORCYCLE,
        "40.00",
        "Lahore",
        "LHE-01",
        CourierAvailability.OFF_DUTY,
        20,
        "3.10",
        96,
        22,
        is_active=False,
    ),
    # A partner courier with no login at all — the reason Courier is a separate
    # entity from User rather than extra columns on it.
    SeedCourier(
        "CR-0006",
        None,
        "Shahid Mehmood (Partner)",
        "+923001110012",
        VehicleType.VAN,
        "600.00",
        "Faisalabad",
        "FSD-01",
        CourierAvailability.AVAILABLE,
        15,
        "4.10",
        64,
        3,
    ),
)


class CourierSeeder(Seeder):
    name = "couriers"
    depends_on = ("users", "warehouses")

    async def run(self, session: AsyncSession) -> int:
        codes = [c.employee_code for c in SEED_COURIERS]
        existing = set(
            (
                await session.scalars(
                    select(Courier.employee_code).where(Courier.employee_code.in_(codes))
                )
            ).all()
        )

        created = 0
        for spec in SEED_COURIERS:
            if spec.employee_code in existing:
                continue
            session.add(
                Courier(
                    id=seed_id("courier", spec.employee_code),
                    user_id=seed_id("user", spec.user_email) if spec.user_email else None,
                    employee_code=spec.employee_code,
                    full_name=spec.full_name,
                    phone=spec.phone,
                    vehicle_type=spec.vehicle_type,
                    vehicle_registration=f"{spec.home_city[:3].upper()}-{spec.employee_code[-4:]}",
                    capacity_kg=Decimal(spec.capacity_kg),
                    can_handle_hazmat=spec.can_handle_hazmat,
                    can_handle_cold_chain=spec.can_handle_cold_chain,
                    availability_status=spec.availability,
                    home_city=spec.home_city,
                    service_cities=list(spec.service_cities) or None,
                    base_warehouse_id=seed_id("warehouse", spec.base_warehouse_code),
                    max_daily_shipments=spec.max_daily_shipments,
                    current_load=spec.current_load,
                    rating=Decimal(spec.rating),
                    total_deliveries=spec.total_deliveries,
                    failed_deliveries=spec.failed_deliveries,
                    is_active=spec.is_active,
                )
            )
            created += 1

        await session.flush()
        return created
