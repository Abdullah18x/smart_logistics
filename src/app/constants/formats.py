"""Shared validation patterns, defaults and naming templates.

Each of these was previously a literal copied into several files by hand — the
phone-number pattern alone was duplicated across five schemas. That is how a
rule quietly drifts: someone loosens the pattern in one place while fixing a
bug, and the other four keep the old, stricter one. One definition here means
one place to change it, and a value that appears in both a Pydantic default
and a SQLAlchemy ``server_default`` can no longer disagree with itself.
"""

# --- Validation patterns ----------------------------------------------------
# Passed straight to Pydantic's `Field(pattern=...)`, so these are strings, not
# compiled patterns.

#: Loose on purpose — it accepts local and international formats without
#: pinning down a country's numbering plan, which is a job for a phone
#: verification provider, not a regex.
PHONE_PATTERN = r"^\+?[0-9\s\-()]{7,32}$"

#: Warehouse, zone and courier employee codes: letters, digits and dashes.
#: These are printed on physical signage and labels, so the character set is
#: kept narrow deliberately.
CODE_PATTERN = r"^[A-Za-z0-9\-]+$"

#: SKU codes additionally allow an underscore, matching conventions inherited
#: from upstream product-catalogue systems that this application does not own.
SKU_CODE_PATTERN = r"^[A-Za-z0-9\-_]+$"


# --- Regional defaults -------------------------------------------------------
# Used as both the Pydantic schema default and the SQLAlchemy server_default
# for the same column, so the two can never quietly diverge.

#: ISO 3166-1 alpha-2. TransFleet's initial market.
DEFAULT_COUNTRY_CODE = "PK"

#: ISO 4217. Declared values, invoicing and stock valuation all default to it.
DEFAULT_CURRENCY = "PKR"

#: IANA name. Operating hours and dispatch cut-offs are interpreted in it
#: unless a warehouse overrides it.
DEFAULT_TIMEZONE = "Asia/Karachi"

#: Metres around a warehouse entrance that count as "at the warehouse", for
#: geofenced arrival and departure events (ADR-015).
DEFAULT_GEOFENCE_RADIUS_M = 150


# --- Naming templates --------------------------------------------------------

#: Shipment reference numbers read as ``SL-<year>-<sequence>``, e.g.
#: ``SL-2026-000123``. The sequence itself comes from a Postgres sequence
#: (``shipments.shipment_reference_seq``), not from this prefix.
SHIPMENT_REFERENCE_PREFIX = "SL-"

#: Package barcodes read as ``PKG`` followed by 16 hex characters.
PACKAGE_BARCODE_PREFIX = "PKG"

#: Deterministic key for one line of one shipment's stock hold — deterministic
#: so a retried reservation attempt can never double-hold the same line twice.
#: ``.format(shipment_id=..., sku_id=...)`` at the call site.
RESERVATION_IDEMPOTENCY_KEY_TEMPLATE = "shipment:{shipment_id}:sku:{sku_id}"
