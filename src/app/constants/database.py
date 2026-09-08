"""Postgres schema names — one per module (ADR-008).

These have no dependents inside ``constants`` and no dependencies of their own,
which is what lets ``models.base`` import them without the two modules ever
needing each other: ``models`` depends on ``constants``, never the reverse.
That direction matters in practice — ``constants.enums`` needs a schema name
for its ``DB_BACKED_ENUMS`` registry, and if it had to import ``models.base``
to get one, loading it would trigger loading every model file (``models
.__init__`` imports them all, so SQLAlchemy sees the full metadata), several
of which import back from ``constants.enums`` — a real circular import, not a
hypothetical one.
"""

IDENTITY_SCHEMA = "identity"
WAREHOUSE_SCHEMA = "warehouses"
CATALOG_SCHEMA = "catalog"
INVENTORY_SCHEMA = "inventory"
SHIPMENT_SCHEMA = "shipments"
COURIER_SCHEMA = "couriers"
PLATFORM_SCHEMA = "platform"
