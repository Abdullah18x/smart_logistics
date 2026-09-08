"""Translating database constraint violations into API responses.

The design decision under test: the database is the authority on integrity, and
the application's job is to turn its failures into something a client can act
on. If this translation is wrong, a legitimate 409 surfaces as a 500.
"""

from sqlalchemy.exc import IntegrityError

from app.core.db_errors import (
    CHECK_VIOLATION,
    CONSTRAINT_MESSAGES,
    FOREIGN_KEY_VIOLATION,
    NOT_NULL_VIOLATION,
    UNIQUE_VIOLATION,
    ConstraintViolationError,
    InvalidReferenceError,
    constraint_name,
    sqlstate,
    translate,
)
from app.core.exceptions import ConflictError


class _RawDriverError(Exception):
    """Stands in for the asyncpg exception SQLAlchemy wraps."""

    def __init__(self, constraint_name: str | None = None, sqlstate: str | None = None) -> None:
        super().__init__("database rejected the statement")
        self.constraint_name = constraint_name
        self.sqlstate = sqlstate


def make_error(constraint: str | None = None, code: str | None = UNIQUE_VIOLATION):
    """Build an IntegrityError shaped the way the asyncpg driver produces one."""
    wrapper = Exception("duplicate key value violates unique constraint")
    wrapper.__cause__ = _RawDriverError(constraint_name=constraint, sqlstate=code)
    return IntegrityError("INSERT INTO ...", {}, wrapper)


class TestExtraction:
    def test_reads_the_constraint_from_the_wrapped_driver_error(self):
        assert constraint_name(make_error("ix_identity_users_email")) == "ix_identity_users_email"

    def test_reads_a_constraint_exposed_directly_on_orig(self):
        """Some drivers put it on the wrapper instead of the cause."""
        wrapper = Exception("boom")
        wrapper.constraint = "uq_users_email"
        assert constraint_name(IntegrityError("...", {}, wrapper)) == "uq_users_email"

    def test_missing_constraint_is_none(self):
        assert constraint_name(make_error(None)) is None

    def test_reads_the_sqlstate(self):
        assert sqlstate(make_error("ix_identity_users_email", UNIQUE_VIOLATION)) == UNIQUE_VIOLATION

    def test_missing_sqlstate_is_none(self):
        assert sqlstate(make_error("uq_users_email", None)) is None


class TestTranslate:
    def test_unique_violation_becomes_a_conflict(self):
        error = translate(make_error("ix_identity_users_email", UNIQUE_VIOLATION))
        assert isinstance(error, ConflictError)
        assert error.status_code == 409

    def test_a_known_constraint_gets_a_human_message(self):
        error = translate(make_error("ix_identity_users_email", UNIQUE_VIOLATION))
        assert error.message == "A user with this email already exists."

    def test_an_unknown_constraint_still_gets_a_sensible_message(self):
        """A new constraint must degrade to a usable 409, never leak SQL."""
        error = translate(make_error("uq_something_nobody_mapped", UNIQUE_VIOLATION))
        assert error.status_code == 409
        assert error.message == "That value is already in use."
        assert "uq_something" not in error.message

    def test_foreign_key_violation_becomes_invalid_reference(self):
        error = translate(make_error("fk_inventory_items_sku_id_skus", FOREIGN_KEY_VIOLATION))
        assert isinstance(error, InvalidReferenceError)
        assert error.status_code == 409
        assert error.code == "invalid_reference"
        assert error.message == "The referenced SKU does not exist."

    def test_check_violation_becomes_a_422(self):
        """A value that breaks a business rule is the client's fault, not a conflict."""
        error = translate(make_error("ck_inventory_items_reserved_within_on_hand", CHECK_VIOLATION))
        assert isinstance(error, ConstraintViolationError)
        assert error.status_code == 422
        assert error.message == "Cannot reserve more stock than is on hand."

    def test_not_null_violation_becomes_a_422(self):
        error = translate(make_error(None, NOT_NULL_VIOLATION))
        assert error.status_code == 422

    def test_an_unrecognised_sqlstate_falls_back_to_a_conflict(self):
        """Better a 409 than a 500: the request broke a rule, the server did not fail."""
        error = translate(make_error(None, "XX000"))
        assert isinstance(error, ConflictError)
        assert error.status_code == 409

    def test_an_error_with_no_diagnostics_at_all_is_still_handled(self):
        assert translate(IntegrityError("...", {}, Exception("opaque"))).status_code == 409


class TestMessageCatalogue:
    def test_every_message_is_written_for_a_human(self):
        for name, message in CONSTRAINT_MESSAGES.items():
            assert message.endswith("."), f"{name} message is not a sentence"
            assert message[0].isupper(), f"{name} message does not start with a capital"

    def test_no_message_leaks_a_constraint_name(self):
        """Internal names are noise to a client and a hint to an attacker."""
        for name, message in CONSTRAINT_MESSAGES.items():
            assert name not in message
