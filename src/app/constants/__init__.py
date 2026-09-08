"""Values the application defines once, for the whole codebase to share.

Two kinds live here, in separate files so each stays easy to scan:

``enums``
    The closed vocabularies backed by a Postgres enum type — user roles,
    shipment status, warehouse type, and so on. Each one already exists as a
    real column constraint the database enforces; this module is what makes
    that enforceable set of values readable and importable from application
    code, instead of something a reader can only discover by opening a
    migration or running ``\\dT`` in ``psql``. ``tests/integration/
    test_enum_constants.py`` is the check and balance: it queries the live
    database and fails if a Python enum's values ever drift from the
    Postgres type backing it.

``formats``
    Regex patterns, default values and naming templates that were previously
    duplicated across several Pydantic schemas and SQLAlchemy models — the
    same phone-number pattern was hand-copied into five files, for one
    example. One definition here means changing a rule changes it
    everywhere it applies.
"""
