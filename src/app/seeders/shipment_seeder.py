"""Sample shipments spread across the lifecycle."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants.enums import (
    AssignmentStatus,
    PackageStatus,
    ServiceLevel,
    ShipmentPriority,
    ShipmentStatus,
)
from app.models.address import Address
from app.models.courier_assignment import CourierAssignment
from app.models.package import Package
from app.models.shipment import Shipment
from app.models.shipment_item import ShipmentItem
from app.models.sku import Sku
from app.seeders.base import Seeder, seed_id

NOW = datetime.now(UTC)


@dataclass(frozen=True)
class SeedShipment:
    reference_no: str
    warehouse_code: str
    status: ShipmentStatus
    service_level: ServiceLevel
    priority: ShipmentPriority
    contact_name: str
    phone: str
    line1: str
    city: str
    latitude: float
    longitude: float
    sku_codes: tuple[tuple[str, int], ...]
    courier_code: str | None = None
    promised_in_hours: int = 48
    attempts: int = 0
    failure_reason: str | None = None
    #: A courier who was offered this shipment and rejected it, before the
    #: current assignee. Exercises the reassignment history path.
    rejected_by_code: str | None = None
    rejection_reason: str | None = None


SEED_SHIPMENTS: tuple[SeedShipment, ...] = (
    SeedShipment(
        "SL-2026-000001",
        "KHI-01",
        ShipmentStatus.CREATED,
        ServiceLevel.STANDARD,
        ShipmentPriority.NORMAL,
        "Ayesha Khan",
        "+923001234501",
        "House 12, Street 4, DHA Phase 6",
        "Karachi",
        24.8007,
        67.0611,
        (("SKU-ELEC-0001", 2), ("SKU-BOOK-0001", 1)),
    ),
    SeedShipment(
        "SL-2026-000002",
        "KHI-01",
        ShipmentStatus.READY_FOR_DISPATCH,
        ServiceLevel.EXPRESS,
        ShipmentPriority.HIGH,
        "Bilal Ahmed",
        "+923001234502",
        "Flat 3B, Clifton Block 5",
        "Karachi",
        24.8138,
        67.0300,
        (("SKU-ELEC-0003", 3),),
        promised_in_hours=24,
    ),
    SeedShipment(
        "SL-2026-000003",
        "LHE-01",
        ShipmentStatus.DISPATCHED,
        ServiceLevel.STANDARD,
        ShipmentPriority.NORMAL,
        "Hina Malik",
        "+923001234503",
        "45-C Gulberg III",
        "Lahore",
        31.5100,
        74.3450,
        (("SKU-APPA-0001", 1), ("SKU-APPA-0002", 1)),
        courier_code="CR-0001",
    ),
    SeedShipment(
        "SL-2026-000004",
        "LHE-01",
        ShipmentStatus.OUT_FOR_DELIVERY,
        ServiceLevel.SAME_DAY,
        ShipmentPriority.CRITICAL,
        "Usman Tariq",
        "+923001234504",
        "House 9, Model Town Link Road",
        "Lahore",
        31.4800,
        74.3200,
        (("SKU-PHRM-0001", 1),),
        courier_code="CR-0002",
        promised_in_hours=6,
    ),
    SeedShipment(
        "SL-2026-000005",
        "ISB-01",
        ShipmentStatus.DELIVERED,
        ServiceLevel.STANDARD,
        ShipmentPriority.NORMAL,
        "Sana Riaz",
        "+923001234505",
        "Street 22, F-8/3",
        "Islamabad",
        33.7100,
        73.0500,
        (("SKU-HOME-0002", 2),),
        courier_code="CR-0003",
        promised_in_hours=-24,
    ),
    # A failed delivery, to exercise retry and return paths.
    SeedShipment(
        "SL-2026-000006",
        "ISB-01",
        ShipmentStatus.DELIVERY_FAILED,
        ServiceLevel.STANDARD,
        ShipmentPriority.HIGH,
        "Kamran Afridi",
        "+923001234506",
        "House 4, Sector G-11/2",
        "Islamabad",
        33.6700,
        72.9900,
        (("SKU-HOME-0001", 1),),
        courier_code="CR-0003",
        promised_in_hours=-6,
        attempts=2,
        failure_reason="Recipient unavailable",
        rejected_by_code="CR-0002",
        rejection_reason="Outside my service area",
    ),
    SeedShipment(
        "SL-2026-000007",
        "KHI-01",
        ShipmentStatus.CANCELLED,
        ServiceLevel.ECONOMY,
        ShipmentPriority.LOW,
        "Zara Sheikh",
        "+923001234507",
        "Plot 88, Gulshan-e-Iqbal",
        "Karachi",
        24.9200,
        67.0900,
        (("SKU-BOOK-0001", 3),),
    ),
)


class ShipmentSeeder(Seeder):
    name = "shipments"
    depends_on = ("warehouses", "skus", "users")

    async def run(self, session: AsyncSession) -> int:
        refs = [s.reference_no for s in SEED_SHIPMENTS]
        existing = set(
            (
                await session.scalars(
                    select(Shipment.reference_no).where(Shipment.reference_no.in_(refs))
                )
            ).all()
        )

        # SKU weights are snapshotted onto each line, so they are read once here.
        skus = {s.code: s for s in (await session.scalars(select(Sku))).all()}

        created = 0
        for spec in SEED_SHIPMENTS:
            if spec.reference_no in existing:
                continue

            address_id = seed_id("address", spec.reference_no)
            session.add(
                Address(
                    id=address_id,
                    contact_name=spec.contact_name,
                    contact_phone=spec.phone,
                    line1=spec.line1,
                    city=spec.city,
                    country_code="PK",
                    latitude=spec.latitude,
                    longitude=spec.longitude,
                )
            )

            shipment_id = seed_id("shipment", spec.reference_no)
            total_weight = sum(
                skus[code].weight_g * qty for code, qty in spec.sku_codes if code in skus
            )
            promised = NOW + timedelta(hours=spec.promised_in_hours)

            session.add(
                Shipment(
                    id=shipment_id,
                    reference_no=spec.reference_no,
                    customer_reference=f"ORD-{spec.reference_no[-6:]}",
                    status=spec.status,
                    service_level=spec.service_level,
                    priority=spec.priority,
                    origin_warehouse_id=seed_id("warehouse", spec.warehouse_code),
                    # Points at the courier profile, not the login (see CourierSeeder).
                    courier_id=(
                        seed_id("courier", spec.courier_code) if spec.courier_code else None
                    ),
                    created_by=seed_id("user", "support.lead@transfleet.com"),
                    destination_address_id=address_id,
                    total_weight_g=total_weight,
                    currency="PKR",
                    promised_delivery_at=promised,
                    dispatched_at=_dispatched_at(spec),
                    picked_up_at=_picked_up_at(spec),
                    delivered_at=NOW - timedelta(hours=20)
                    if spec.status is ShipmentStatus.DELIVERED
                    else None,
                    cancelled_at=NOW - timedelta(hours=2)
                    if spec.status is ShipmentStatus.CANCELLED
                    else None,
                    delivery_attempt_count=spec.attempts,
                    failure_reason=spec.failure_reason,
                )
            )

            for code, quantity in spec.sku_codes:
                sku = skus.get(code)
                if sku is None:
                    continue
                session.add(
                    ShipmentItem(
                        id=seed_id("shipment_item", f"{spec.reference_no}:{code}"),
                        shipment_id=shipment_id,
                        sku_id=sku.id,
                        quantity=quantity,
                        sku_code=sku.code,
                        sku_name=sku.name,
                        unit_weight_g=sku.weight_g,
                        unit_value=sku.unit_value,
                    )
                )

            # Assignment history. shipments.courier_id records who holds it now;
            # these rows record how it got there.
            sequence = 1
            if spec.rejected_by_code:
                session.add(
                    CourierAssignment(
                        id=seed_id("assignment", f"{spec.reference_no}:{spec.rejected_by_code}"),
                        courier_id=seed_id("courier", spec.rejected_by_code),
                        shipment_id=shipment_id,
                        status=AssignmentStatus.REJECTED,
                        sequence_no=sequence,
                        assigned_at=NOW - timedelta(hours=32),
                        responded_at=NOW - timedelta(hours=31, minutes=48),
                        reason=spec.rejection_reason,
                    )
                )
                sequence += 1

            if spec.courier_code:
                session.add(
                    CourierAssignment(
                        id=seed_id("assignment", f"{spec.reference_no}:{spec.courier_code}"),
                        courier_id=seed_id("courier", spec.courier_code),
                        shipment_id=shipment_id,
                        status=(
                            AssignmentStatus.COMPLETED
                            if spec.status is ShipmentStatus.DELIVERED
                            else AssignmentStatus.ACCEPTED
                        ),
                        sequence_no=sequence,
                        assigned_at=NOW - timedelta(hours=30),
                        responded_at=NOW - timedelta(hours=29, minutes=55),
                        completed_at=(
                            NOW - timedelta(hours=20)
                            if spec.status is ShipmentStatus.DELIVERED
                            else None
                        ),
                    )
                )

            if spec.status is not ShipmentStatus.CREATED and total_weight:
                session.add(
                    Package(
                        id=seed_id("package", spec.reference_no),
                        shipment_id=shipment_id,
                        sequence_no=1,
                        barcode=f"PKG{spec.reference_no.replace('-', '')}01",
                        status=_package_status(spec.status),
                        weight_g=total_weight,
                        packed_at=NOW - timedelta(hours=30),
                    )
                )
            created += 1

        await session.flush()
        return created


_DISPATCHED = {
    ShipmentStatus.DISPATCHED,
    ShipmentStatus.PICKED_UP,
    ShipmentStatus.IN_TRANSIT,
    ShipmentStatus.OUT_FOR_DELIVERY,
    ShipmentStatus.DELIVERED,
    ShipmentStatus.DELIVERY_FAILED,
}
_IN_CUSTODY = _DISPATCHED - {ShipmentStatus.DISPATCHED}


def _dispatched_at(spec: SeedShipment) -> datetime | None:
    return NOW - timedelta(hours=28) if spec.status in _DISPATCHED else None


def _picked_up_at(spec: SeedShipment) -> datetime | None:
    return NOW - timedelta(hours=26) if spec.status in _IN_CUSTODY else None


def _package_status(status: ShipmentStatus) -> PackageStatus:
    if status in _DISPATCHED:
        return PackageStatus.DISPATCHED
    if status is ShipmentStatus.READY_FOR_DISPATCH:
        return PackageStatus.LABELLED
    return PackageStatus.PACKED
