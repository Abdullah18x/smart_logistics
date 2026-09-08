"""Translate database constraint violations into domain errors.

The database is the authority on referential integrity, uniqueness and range
checks. Rather than duplicating those rules in Python — where they drift, and
where a check-then-act is a race anyway — we let the constraint fire and turn
the failure into a meaningful API response here.

Constraint names are stable because ``models.base`` fixes them with a naming
convention, so mapping them to messages is safe.
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from app.core.exceptions import ConflictError, DomainError

# Postgres SQLSTATE codes.
UNIQUE_VIOLATION = "23505"
FOREIGN_KEY_VIOLATION = "23503"
CHECK_VIOLATION = "23514"
NOT_NULL_VIOLATION = "23502"


class InvalidReferenceError(DomainError):
    """A referenced row does not exist, or is still referenced by others."""

    status_code = 409
    code = "invalid_reference"


class ConstraintViolationError(DomainError):
    """A value broke a database rule."""

    status_code = 422
    code = "constraint_violation"


#: Friendly messages for constraints a client can realistically trip.
CONSTRAINT_MESSAGES: dict[str, str] = {
    # Uniqueness
    "ix_identity_users_email": "A user with this email already exists.",
    "ix_warehouses_warehouses_code": "A warehouse with this code already exists.",
    "uq_warehouse_zone_code": "This zone code is already used in this warehouse.",
    "uq_warehouse_operating_day": "This weekday already has opening hours.",
    "ix_catalog_skus_code": "A SKU with this code already exists.",
    "uq_inventory_warehouse_sku": "A stock position already exists for this SKU at this warehouse.",
    "ix_shipments_shipments_reference_no": "A shipment with this reference already exists.",
    "uq_shipment_item_sku": "This SKU is already on the shipment.",
    "uq_package_sequence": "A package with this sequence number already exists.",
    "ix_shipments_packages_barcode": "This barcode is already in use.",
    "ix_couriers_couriers_employee_code": "A courier with this employee code already exists.",
    "uq_idempotency_keys_key": "This idempotency key has already been used.",
    # Range and business invariants
    "ck_inventory_items_reserved_within_on_hand": ("Cannot reserve more stock than is on hand."),
    "ck_inventory_items_on_hand_non_negative": "Stock on hand cannot go negative.",
    "ck_inventory_items_reserved_non_negative": "Reserved stock cannot go negative.",
    "ck_warehouse_operating_hours_valid_window": (
        "A day must either be closed, or open with a closing time after its opening time."
    ),
    "ck_warehouses_capacity_positive": "Capacity must be greater than zero.",
    "ck_warehouse_zones_capacity_positive": "Zone capacity must be greater than zero.",
    "ck_shipment_items_quantity_positive": "Item quantity must be greater than zero.",
    # References
    "fk_inventory_items_sku_id_skus": "The referenced SKU does not exist.",
    "fk_inventory_items_warehouse_id_warehouses": "The referenced warehouse does not exist.",
    "fk_shipments_origin_warehouse_id_warehouses": "The origin warehouse does not exist.",
    "fk_shipments_courier_id_couriers": "The referenced courier does not exist.",
    "fk_shipment_items_sku_id_skus": "One or more referenced SKUs do not exist.",
    "fk_shipments_destination_address_id_addresses": "The destination address does not exist.",
    "fk_couriers_user_id_users": "The referenced user account does not exist.",
    "fk_user_warehouse_assignments_warehouse_id_warehouses": (
        "The referenced warehouse does not exist."
    ),
}


def constraint_name(error: IntegrityError) -> str | None:
    original = getattr(error, "orig", None)
    # asyncpg exposes the constraint on the wrapped exception.
    for attribute in ("constraint_name", "constraint"):
        name = getattr(original, attribute, None)
        if name:
            return str(name)
    cause = getattr(original, "__cause__", None)
    name = getattr(cause, "constraint_name", None)
    # str(None) is "None", which is truthy — return a real absence instead.
    return str(name) if name else None


def sqlstate(error: IntegrityError) -> str | None:
    original = getattr(error, "orig", None)
    cause = getattr(original, "__cause__", original)
    return getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)


def translate(error: IntegrityError) -> DomainError:
    """Map an ``IntegrityError`` to the domain error the API should return."""
    name = constraint_name(error)
    message = CONSTRAINT_MESSAGES.get(name or "")
    code = sqlstate(error)

    if code == UNIQUE_VIOLATION:
        return ConflictError(message or "That value is already in use.")
    if code == FOREIGN_KEY_VIOLATION:
        return InvalidReferenceError(
            message or "A referenced record does not exist, or is still in use by another record."
        )
    if code in (CHECK_VIOLATION, NOT_NULL_VIOLATION):
        return ConstraintViolationError(message or "The request violates a data rule.")
    # Unknown integrity failure: surface it as a conflict rather than a 500,
    # since it is the request that broke a rule, not the server.
    return ConflictError(message or "The request conflicts with existing data.")
