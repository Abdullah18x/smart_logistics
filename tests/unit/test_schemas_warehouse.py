"""Warehouse, zone and operating-hours contracts."""

from datetime import time

import pytest
from pydantic import ValidationError

from app.core.enums import Weekday, ZoneType
from app.schemas.warehouse import WarehouseCreate, WarehouseUpdate
from app.schemas.warehouse_operating_hours import OperatingHoursWrite, WeeklyScheduleUpdate
from app.schemas.warehouse_zone import WarehouseZoneCreate

VALID = {
    "code": "khi-02",
    "name": "Karachi North",
    "address_line1": "Plot 42, Korangi",
    "city": "Karachi",
    "capacity_units": 50_000,
}


class TestCode:
    def test_code_is_uppercased_and_trimmed(self):
        """Codes are printed on labels; casing must not vary by who typed it."""
        assert WarehouseCreate(**VALID | {"code": "  khi-02 "}).code == "KHI-02"

    @pytest.mark.parametrize("code", ["KHI 02", "KHI_02", "KHI@02", "K"])
    def test_invalid_codes_are_rejected(self, code):
        with pytest.raises(ValidationError):
            WarehouseCreate(**VALID | {"code": code})

    def test_code_cannot_be_changed_after_creation(self):
        """It is referenced externally, so renaming would break those references."""
        assert "code" not in WarehouseUpdate.model_fields

    def test_status_is_not_editable_through_a_general_update(self):
        """Taking a facility offline has side effects, so it has its own endpoint."""
        assert "status" not in WarehouseUpdate.model_fields


class TestGeolocation:
    def test_valid_coordinates_are_accepted(self):
        warehouse = WarehouseCreate(**VALID | {"latitude": 24.8607, "longitude": 67.0011})
        assert warehouse.latitude == 24.8607

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("latitude", 91),
            ("latitude", -91),
            ("longitude", 181),
            ("longitude", -181),
            ("entrance_latitude", 100),
            ("entrance_longitude", -200),
        ],
    )
    def test_out_of_range_coordinates_are_rejected(self, field, value):
        with pytest.raises(ValidationError):
            WarehouseCreate(**VALID | {field: value})

    def test_the_geofence_defaults_to_150_metres(self):
        assert WarehouseCreate(**VALID).geofence_radius_m == 150

    @pytest.mark.parametrize("radius", [0, -1, 5001])
    def test_an_unusable_geofence_is_rejected(self, radius):
        with pytest.raises(ValidationError):
            WarehouseCreate(**VALID | {"geofence_radius_m": radius})


class TestTimezone:
    def test_the_default_is_a_real_zone(self):
        assert WarehouseCreate(**VALID).timezone == "Asia/Karachi"

    def test_a_known_zone_is_accepted(self):
        assert WarehouseCreate(**VALID | {"timezone": "Europe/London"}).timezone == "Europe/London"

    def test_an_unknown_zone_is_rejected(self):
        """Operating hours are meaningless without a zone to interpret them in."""
        with pytest.raises(ValidationError, match="Unknown IANA timezone"):
            WarehouseCreate(**VALID | {"timezone": "Mars/Olympus_Mons"})


class TestCountryAndCapacity:
    def test_country_code_is_uppercased(self):
        assert WarehouseCreate(**VALID | {"country_code": "pk"}).country_code == "PK"

    def test_country_code_must_be_two_letters(self):
        with pytest.raises(ValidationError):
            WarehouseCreate(**VALID | {"country_code": "PAK"})

    @pytest.mark.parametrize("capacity", [0, -100])
    def test_capacity_must_be_positive(self, capacity):
        with pytest.raises(ValidationError):
            WarehouseCreate(**VALID | {"capacity_units": capacity})

    def test_capacity_is_required(self):
        with pytest.raises(ValidationError):
            WarehouseCreate(**{k: v for k, v in VALID.items() if k != "capacity_units"})


class TestNestedCreation:
    def test_zones_and_hours_may_be_supplied_inline(self):
        """So a facility can be created ready to use in one request."""
        warehouse = WarehouseCreate(
            **VALID
            | {
                "zones": [{"code": "a1", "name": "Ambient A1", "capacity_units": 5000}],
                "operating_hours": [
                    {"day_of_week": 1, "opens_at": "08:00:00", "closes_at": "20:00:00"}
                ],
            }
        )
        assert warehouse.zones[0].code == "A1"
        assert warehouse.operating_hours[0].day_of_week is Weekday.MONDAY

    def test_both_collections_default_to_empty(self):
        warehouse = WarehouseCreate(**VALID)
        assert warehouse.zones == [] and warehouse.operating_hours == []

    def test_more_than_seven_days_is_rejected(self):
        day = {"day_of_week": 1, "opens_at": "08:00:00", "closes_at": "20:00:00"}
        with pytest.raises(ValidationError):
            WarehouseCreate(**VALID | {"operating_hours": [day] * 8})


class TestZones:
    def test_zone_code_is_uppercased(self):
        zone = WarehouseZoneCreate(code=" a1 ", name="Ambient A1", capacity_units=100)
        assert zone.code == "A1"

    def test_zone_defaults_to_storage(self):
        zone = WarehouseZoneCreate(code="A1", name="Ambient A1", capacity_units=100)
        assert zone.type is ZoneType.STORAGE

    def test_zone_capacity_must_be_positive(self):
        with pytest.raises(ValidationError):
            WarehouseZoneCreate(code="A1", name="Ambient A1", capacity_units=0)


class TestOperatingHours:
    def test_a_normal_open_day_is_accepted(self):
        day = OperatingHoursWrite(day_of_week=1, opens_at=time(8), closes_at=time(20))
        assert day.is_closed is False

    def test_a_closed_day_needs_no_times(self):
        assert OperatingHoursWrite(day_of_week=7, is_closed=True).opens_at is None

    def test_a_closed_day_must_not_carry_times(self):
        """Contradictory input is a client bug, not something to silently reconcile."""
        with pytest.raises(ValidationError, match="closed day must not have opening times"):
            OperatingHoursWrite(day_of_week=7, is_closed=True, opens_at=time(8))

    def test_an_open_day_needs_both_times(self):
        with pytest.raises(ValidationError, match="requires both"):
            OperatingHoursWrite(day_of_week=1, opens_at=time(8))

    def test_closing_before_opening_is_rejected(self):
        with pytest.raises(ValidationError, match="later than"):
            OperatingHoursWrite(day_of_week=1, opens_at=time(20), closes_at=time(8))

    def test_closing_at_the_opening_time_is_rejected(self):
        with pytest.raises(ValidationError):
            OperatingHoursWrite(day_of_week=1, opens_at=time(8), closes_at=time(8))

    def test_days_are_iso_numbered_from_monday(self):
        assert OperatingHoursWrite(day_of_week=1, is_closed=True).day_of_week is Weekday.MONDAY
        assert OperatingHoursWrite(day_of_week=7, is_closed=True).day_of_week is Weekday.SUNDAY

    @pytest.mark.parametrize("day", [0, 8])
    def test_a_day_outside_one_to_seven_is_rejected(self, day):
        with pytest.raises(ValidationError):
            OperatingHoursWrite(day_of_week=day, is_closed=True)


class TestWeeklySchedule:
    def test_a_full_week_is_accepted(self):
        schedule = WeeklyScheduleUpdate(
            days=[{"day_of_week": d, "is_closed": True} for d in range(1, 8)]
        )
        assert len(schedule.days) == 7

    def test_a_repeated_day_is_rejected(self):
        """Two Mondays would leave the resulting schedule ambiguous."""
        with pytest.raises(ValidationError, match="only once"):
            WeeklyScheduleUpdate(
                days=[
                    {"day_of_week": 1, "is_closed": True},
                    {"day_of_week": 1, "opens_at": "08:00:00", "closes_at": "20:00:00"},
                ]
            )

    def test_an_empty_schedule_is_rejected(self):
        with pytest.raises(ValidationError):
            WeeklyScheduleUpdate(days=[])
