"""Shipment, package and address contracts."""

import uuid
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.constants.enums import ServiceLevel, ShipmentPriority
from app.schemas.address import AddressCreate
from app.schemas.inventory import StockAdjustment
from app.schemas.package import PackageCreate
from app.schemas.shipment import (
    ShipmentCancel,
    ShipmentCreate,
    ShipmentItemCreate,
    ShipmentUpdate,
)

ADDRESS = {
    "contact_name": "Ayesha Khan",
    "contact_phone": "+923001234567",
    "line1": "House 12, Street 4, DHA Phase 6",
    "city": "Karachi",
}
VALID = {
    "origin_warehouse_id": uuid.uuid4(),
    "destination_address": ADDRESS,
    "items": [{"sku_id": uuid.uuid4(), "quantity": 2}],
}


class TestShipmentCreate:
    def test_a_minimal_shipment_is_accepted(self):
        shipment = ShipmentCreate(**VALID)
        assert shipment.service_level is ServiceLevel.STANDARD
        assert shipment.priority is ShipmentPriority.NORMAL
        assert shipment.currency == "PKR"

    def test_at_least_one_item_is_required(self):
        """An empty shipment has nothing to reserve, pack or deliver."""
        with pytest.raises(ValidationError):
            ShipmentCreate(**VALID | {"items": []})

    def test_an_origin_warehouse_is_required(self):
        with pytest.raises(ValidationError):
            ShipmentCreate(**{k: v for k, v in VALID.items() if k != "origin_warehouse_id"})

    def test_the_destination_is_supplied_inline_not_by_id(self):
        """The address is a snapshot; editing a saved one must not rewrite history."""
        assert "destination_address_id" not in ShipmentCreate.model_fields
        assert ShipmentCreate(**VALID).destination_address.city == "Karachi"

    def test_packages_are_optional(self):
        assert ShipmentCreate(**VALID).packages == []

    def test_declared_value_cannot_be_negative(self):
        with pytest.raises(ValidationError):
            ShipmentCreate(**VALID | {"declared_value": Decimal("-1.00")})

    def test_declared_value_keeps_two_decimal_places(self):
        assert ShipmentCreate(**VALID | {"declared_value": "1234.56"}).declared_value == Decimal(
            "1234.56"
        )

    def test_currency_must_be_a_three_letter_code(self):
        with pytest.raises(ValidationError):
            ShipmentCreate(**VALID | {"currency": "RUPEES"})

    def test_an_unknown_service_level_is_rejected(self):
        with pytest.raises(ValidationError):
            ShipmentCreate(**VALID | {"service_level": "teleport"})

    def test_too_many_lines_are_rejected(self):
        line = {"sku_id": str(uuid.uuid4()), "quantity": 1}
        with pytest.raises(ValidationError):
            ShipmentCreate(**VALID | {"items": [line] * 201})


class TestShipmentItem:
    @pytest.mark.parametrize("quantity", [0, -1])
    def test_quantity_must_be_positive(self, quantity):
        with pytest.raises(ValidationError):
            ShipmentItemCreate(sku_id=uuid.uuid4(), quantity=quantity)

    def test_an_absurd_quantity_is_rejected(self):
        with pytest.raises(ValidationError):
            ShipmentItemCreate(sku_id=uuid.uuid4(), quantity=100_001)

    def test_sku_attributes_cannot_be_supplied_by_the_client(self):
        """They are snapshotted server-side from the catalogue, never trusted."""
        for field in ("sku_code", "sku_name", "unit_weight_g", "unit_value"):
            assert field not in ShipmentItemCreate.model_fields


class TestShipmentUpdate:
    def test_status_cannot_be_set_through_a_general_update(self):
        """Transitions go through their own endpoint so each can enforce its rules."""
        assert "status" not in ShipmentUpdate.model_fields

    def test_items_and_origin_are_not_editable(self):
        assert "items" not in ShipmentUpdate.model_fields
        assert "origin_warehouse_id" not in ShipmentUpdate.model_fields

    def test_every_field_is_optional(self):
        assert ShipmentUpdate().model_dump(exclude_unset=True) == {}

    def test_only_the_supplied_fields_are_reported_as_set(self):
        """The service applies exclude_unset, so this is what makes PATCH partial."""
        update = ShipmentUpdate(priority=ShipmentPriority.HIGH)
        assert update.model_dump(exclude_unset=True) == {"priority": ShipmentPriority.HIGH}


class TestCancellation:
    def test_a_reason_is_required(self):
        """Cancellations release stock and are audited; 'why' is not optional."""
        with pytest.raises(ValidationError):
            ShipmentCancel()

    def test_a_token_reason_is_rejected(self):
        with pytest.raises(ValidationError):
            ShipmentCancel(reason="x")

    def test_a_real_reason_is_accepted(self):
        assert ShipmentCancel(reason="Customer cancelled the order").reason


class TestPackages:
    def test_a_valid_parcel_is_accepted(self):
        parcel = PackageCreate(weight_g=1200, length_mm=400, width_mm=300, height_mm=200)
        assert parcel.weight_g == 1200

    def test_dimensions_are_optional(self):
        assert PackageCreate(weight_g=1200).length_mm is None

    @pytest.mark.parametrize("weight", [0, -1, 1_000_001])
    def test_an_impossible_weight_is_rejected(self, weight):
        with pytest.raises(ValidationError):
            PackageCreate(weight_g=weight)

    def test_a_zero_dimension_is_rejected(self):
        with pytest.raises(ValidationError):
            PackageCreate(weight_g=100, length_mm=0)

    def test_the_barcode_is_not_client_supplied(self):
        """Scanners read it, so the system issues it."""
        assert "barcode" not in PackageCreate.model_fields


class TestAddress:
    def test_country_code_is_uppercased(self):
        assert AddressCreate(**ADDRESS | {"country_code": "pk"}).country_code == "PK"

    def test_contact_details_are_required(self):
        with pytest.raises(ValidationError):
            AddressCreate(**{k: v for k, v in ADDRESS.items() if k != "contact_phone"})

    def test_a_malformed_phone_is_rejected(self):
        with pytest.raises(ValidationError):
            AddressCreate(**ADDRESS | {"contact_phone": "call me"})

    def test_coordinates_are_range_checked(self):
        with pytest.raises(ValidationError):
            AddressCreate(**ADDRESS | {"latitude": 95.0})

    def test_delivery_instructions_are_length_limited(self):
        with pytest.raises(ValidationError):
            AddressCreate(**ADDRESS | {"delivery_instructions": "x" * 501})


class TestStockAdjustment:
    def test_a_zero_adjustment_is_rejected(self):
        """Nothing changed, so there is nothing to record in the ledger."""
        with pytest.raises(ValidationError, match="must not be zero"):
            StockAdjustment(type="adjustment", quantity_delta=0)

    def test_positive_and_negative_deltas_are_both_valid(self):
        assert StockAdjustment(type="inbound_receipt", quantity_delta=250).quantity_delta == 250
        assert StockAdjustment(type="damage_write_off", quantity_delta=-5).quantity_delta == -5

    def test_an_unknown_movement_type_is_rejected(self):
        with pytest.raises(ValidationError):
            StockAdjustment(type="shrinkage", quantity_delta=1)
