"""The check and balance for ``constants.enums``.

A Postgres enum type and the Python enum that generated it can drift apart in
either direction: someone adds a member to the Python class and forgets the
migration, or a migration is hand-edited and the Python side is never told.
Either way the mismatch is invisible until a value the database accepts gets
rejected by Pydantic, or a value the application thinks is valid is refused by
the database — usually far from whichever line actually changed.

This queries the live database directly (bypassing SQLAlchemy's own enum
handling, which would just re-report what the Python side already believes)
and compares Postgres's own label list against ``DB_BACKED_ENUMS``.
"""

from sqlalchemy import text

from app.constants.enums import DB_BACKED_ENUMS


async def _live_labels(db_session, *, schema: str, pg_name: str) -> set[str]:
    """The exact labels Postgres holds for one enum type, read from its catalog."""
    rows = await db_session.scalars(
        text(
            "SELECT e.enumlabel FROM pg_enum e "
            "JOIN pg_type t ON t.oid = e.enumtypid "
            "JOIN pg_namespace n ON n.oid = t.typnamespace "
            "WHERE n.nspname = :schema AND t.typname = :pg_name"
        ).bindparams(schema=schema, pg_name=pg_name)
    )
    return set(rows.all())


class TestEveryDbBackedEnumMatchesItsPostgresType:
    async def test_the_registry_is_not_empty(self):
        """A drift check with nothing to check catches nothing."""
        assert len(DB_BACKED_ENUMS) >= 14

    async def test_every_registered_type_exists_in_the_database(self, db_session):
        """A registry entry pointing at a type that was never migrated."""
        for spec in DB_BACKED_ENUMS:
            labels = await _live_labels(db_session, schema=spec.schema, pg_name=spec.pg_name)
            assert labels, (
                f"{spec.enum_class.__name__}: no Postgres enum type "
                f"'{spec.schema}.{spec.pg_name}' — check DB_BACKED_ENUMS or the migration"
            )

    async def test_python_and_postgres_agree_on_every_member(self, db_session):
        """The actual drift check: same set of values, in both directions.

        A member missing from Postgres means the database will reject a value
        the application considers valid; a label missing from Python means a
        value the database accepts has no corresponding enum member.
        """
        mismatches = []
        for spec in DB_BACKED_ENUMS:
            python_values = {member.value for member in spec.enum_class}
            live_labels = await _live_labels(db_session, schema=spec.schema, pg_name=spec.pg_name)
            if python_values != live_labels:
                mismatches.append(
                    f"{spec.enum_class.__name__} ({spec.schema}.{spec.pg_name}): "
                    f"python-only={python_values - live_labels or None}, "
                    f"postgres-only={live_labels - python_values or None}"
                )
        assert not mismatches, "enum drift found:\n" + "\n".join(mismatches)

    async def test_no_enum_is_registered_twice_for_the_same_type(self):
        """Two specs pointing at one type would make the drift check ambiguous."""
        seen = [(spec.schema, spec.pg_name) for spec in DB_BACKED_ENUMS]
        assert len(seen) == len(set(seen))
