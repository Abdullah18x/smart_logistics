"""Development warehouses: ``python -m warehouse.seed``.

Ids are uuid5(code), the same derivation Identity and Inventory use, so
operator scopes and stock positions line up without any shared table. Each
warehouse also stages a ``warehouse.created`` event, which is how Shipment and
Inventory build their local copies.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, time

from sqlalchemy import select

from warehouse.config import settings
from warehouse.db import database
from warehouse.events import WarehouseEvents, emit
from warehouse.models import (
    Warehouse,
    WarehouseOperatingHours,
    WarehouseStatus,
    WarehouseType,
    WarehouseZone,
    Weekday,
    ZoneType,
)

logger = logging.getLogger("warehouse.seed")
SEED_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")


def seed_id(entity: str, key: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, f"{entity}:{key}")


@dataclass(frozen=True)
class SeedZone:
    code: str
    name: str
    type: ZoneType
    capacity_units: int


@dataclass(frozen=True)
class SeedWarehouse:
    code: str
    name: str
    type: WarehouseType
    address_line1: str
    city: str
    region: str
    postal_code: str
    latitude: float
    longitude: float
    capacity_units: int
    max_daily_outbound: int
    contact_name: str
    contact_phone: str
    #: Gate the courier drives to; offset slightly from the centroid.
    entrance_latitude: float | None = None
    entrance_longitude: float | None = None
    geofence_radius_m: int = 150
    status: WarehouseStatus = WarehouseStatus.ACTIVE
    zones: tuple[SeedZone, ...] = field(default_factory=tuple)
    #: Weekdays the facility is closed. All other days use the standard window.
    closed_days: tuple[Weekday, ...] = (Weekday.SUNDAY,)
    opens_at: time = time(8, 0)
    closes_at: time = time(20, 0)


def _standard_zones(prefix: str, storage_units: int) -> tuple[SeedZone, ...]:
    """The zone layout every full facility shares, following goods flow."""
    return (
        SeedZone(f"{prefix}-RCV", "Receiving Dock", ZoneType.RECEIVING, 2000),
        SeedZone(f"{prefix}-STO", "Ambient Storage", ZoneType.STORAGE, storage_units),
        SeedZone(f"{prefix}-PCK", "Picking Aisles", ZoneType.PICKING, 3000),
        SeedZone(f"{prefix}-PAK", "Packing Benches", ZoneType.PACKING, 1500),
        SeedZone(f"{prefix}-STG", "Outbound Staging", ZoneType.STAGING, 2500),
        SeedZone(f"{prefix}-DSP", "Dispatch Bay", ZoneType.DISPATCH, 2000),
    )


SEED_WAREHOUSES: tuple[SeedWarehouse, ...] = (
    SeedWarehouse(
        code="KHI-01",
        name="Karachi Central Fulfilment Centre",
        type=WarehouseType.FULFILLMENT_CENTER,
        address_line1="Plot 24, Korangi Industrial Area",
        city="Karachi",
        region="Sindh",
        postal_code="74900",
        latitude=24.8607,
        longitude=67.0011,
        capacity_units=60000,
        max_daily_outbound=5000,
        contact_name="Imran Sheikh",
        contact_phone="+922135000001",
        entrance_latitude=24.8611,
        entrance_longitude=67.0018,
        geofence_radius_m=200,
        zones=_standard_zones("KHI", 40000),
    ),
    SeedWarehouse(
        code="LHE-01",
        name="Lahore Distribution Hub",
        type=WarehouseType.DISTRIBUTION_HUB,
        address_line1="Block C, Sundar Industrial Estate",
        city="Lahore",
        region="Punjab",
        postal_code="54000",
        latitude=31.5204,
        longitude=74.3587,
        capacity_units=45000,
        max_daily_outbound=3800,
        contact_name="Nadia Iqbal",
        contact_phone="+924235000001",
        entrance_latitude=31.5209,
        entrance_longitude=74.3594,
        geofence_radius_m=180,
        zones=_standard_zones("LHE", 30000),
    ),
    SeedWarehouse(
        code="ISB-01",
        name="Islamabad Regional Depot",
        type=WarehouseType.REGIONAL_DEPOT,
        address_line1="Sector I-9 Industrial Area",
        city="Islamabad",
        region="Islamabad Capital Territory",
        postal_code="44000",
        latitude=33.6844,
        longitude=73.0479,
        capacity_units=20000,
        max_daily_outbound=1500,
        contact_name="Usman Tariq",
        contact_phone="+925135000001",
        entrance_latitude=33.6848,
        entrance_longitude=73.0485,
        geofence_radius_m=120,
        zones=_standard_zones("ISB", 12000),
    ),
    SeedWarehouse(
        code="FSD-01",
        name="Faisalabad Returns Centre",
        type=WarehouseType.RETURNS_CENTER,
        address_line1="Sargodha Road Logistics Park",
        city="Faisalabad",
        region="Punjab",
        postal_code="38000",
        latitude=31.4504,
        longitude=73.1350,
        capacity_units=15000,
        max_daily_outbound=800,
        contact_name="Rabia Saleem",
        contact_phone="+924135000001",
        entrance_latitude=31.4508,
        entrance_longitude=73.1357,
        geofence_radius_m=120,
        zones=(
            SeedZone("FSD-RCV", "Returns Intake", ZoneType.RECEIVING, 3000),
            SeedZone("FSD-QAR", "Quarantine Hold", ZoneType.QUARANTINE, 2000),
            SeedZone("FSD-RET", "Returns Grading", ZoneType.RETURNS, 5000),
            SeedZone("FSD-STO", "Restock Storage", ZoneType.STORAGE, 5000),
        ),
        closed_days=(Weekday.SATURDAY, Weekday.SUNDAY),
        opens_at=time(9, 0),
        closes_at=time(18, 0),
    ),
    # Deliberately offline, to exercise status handling in dispatch.
    SeedWarehouse(
        code="PEW-01",
        name="Peshawar Forward Depot",
        type=WarehouseType.REGIONAL_DEPOT,
        address_line1="Hayatabad Industrial Estate, Phase 3",
        city="Peshawar",
        region="Khyber Pakhtunkhwa",
        postal_code="25000",
        latitude=34.0151,
        longitude=71.5249,
        capacity_units=9000,
        max_daily_outbound=600,
        contact_name="Kamran Afridi",
        contact_phone="+919135000001",
        entrance_latitude=34.0155,
        entrance_longitude=71.5255,
        geofence_radius_m=100,
        status=WarehouseStatus.MAINTENANCE,
        zones=(
            SeedZone("PEW-RCV", "Receiving Dock", ZoneType.RECEIVING, 1000),
            SeedZone("PEW-STO", "Ambient Storage", ZoneType.STORAGE, 6000),
            SeedZone("PEW-DSP", "Dispatch Bay", ZoneType.DISPATCH, 1000),
        ),
    ),
)


async def seed() -> int:
    if settings.is_production:
        raise RuntimeError("Refusing to seed a production database.")
    codes = [w.code for w in SEED_WAREHOUSES]
    created = 0
    async with database.scope() as session:
        existing = set(
            (await session.scalars(select(Warehouse.code).where(Warehouse.code.in_(codes)))).all()
        )
        for spec in SEED_WAREHOUSES:
            if spec.code in existing:
                continue
            warehouse_id = seed_id("warehouse", spec.code)
            warehouse = Warehouse(
                id=warehouse_id,
                code=spec.code,
                name=spec.name,
                type=spec.type,
                status=spec.status,
                address_line1=spec.address_line1,
                city=spec.city,
                region=spec.region,
                postal_code=spec.postal_code,
                country_code="PK",
                latitude=spec.latitude,
                longitude=spec.longitude,
                entrance_latitude=spec.entrance_latitude,
                entrance_longitude=spec.entrance_longitude,
                geofence_radius_m=spec.geofence_radius_m,
                geocoded_at=datetime.now(UTC),
                capacity_units=spec.capacity_units,
                max_daily_outbound=spec.max_daily_outbound,
                timezone="Asia/Karachi",
                contact_name=spec.contact_name,
                contact_email=f"{spec.code.lower()}@transfleet.com",
                contact_phone=spec.contact_phone,
            )
            session.add(warehouse)
            for zone in spec.zones:
                session.add(
                    WarehouseZone(
                        id=seed_id("warehouse_zone", f"{spec.code}:{zone.code}"),
                        warehouse_id=warehouse_id,
                        code=zone.code,
                        name=zone.name,
                        type=zone.type,
                        capacity_units=zone.capacity_units,
                    )
                )
            for day in Weekday:
                closed = day in spec.closed_days
                session.add(
                    WarehouseOperatingHours(
                        id=seed_id("warehouse_hours", f"{spec.code}:{day.value}"),
                        warehouse_id=warehouse_id,
                        day_of_week=day,
                        opens_at=None if closed else spec.opens_at,
                        closes_at=None if closed else spec.closes_at,
                        is_closed=closed,
                    )
                )
            await session.flush()
            emit(session, WarehouseEvents.CREATED, warehouse)
            created += 1
    return created


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
    logger.info("warehouse: %d warehouse(s) created", asyncio.run(seed()))


if __name__ == "__main__":
    main()
