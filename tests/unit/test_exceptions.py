"""Domain exceptions carry their own HTTP status, so no service imports FastAPI."""

import pytest

from app.core.db_errors import ConstraintViolationError, InvalidReferenceError
from app.core.exceptions import (
    AccountInactiveError,
    AccountLockedError,
    AuthenticationError,
    ConflictError,
    DomainError,
    NotFoundError,
    PermissionDeniedError,
)
from app.services.idempotency_service import IdempotencyConflictError, RequestInProgressError
from app.services.inventory_service import InsufficientStockError


@pytest.mark.parametrize(
    ("error_class", "status_code", "code"),
    [
        (DomainError, 400, "domain_error"),
        (NotFoundError, 404, "not_found"),
        (ConflictError, 409, "conflict"),
        (AuthenticationError, 401, "authentication_failed"),
        (PermissionDeniedError, 403, "permission_denied"),
        (AccountLockedError, 401, "account_locked"),
        (AccountInactiveError, 401, "account_inactive"),
        (InvalidReferenceError, 409, "invalid_reference"),
        (ConstraintViolationError, 422, "constraint_violation"),
        (InsufficientStockError, 409, "insufficient_stock"),
        (IdempotencyConflictError, 422, "idempotency_key_reuse"),
        (RequestInProgressError, 409, "request_in_progress"),
    ],
)
def test_each_error_maps_to_a_stable_status_and_code(error_class, status_code, code):
    """These codes are a public contract — clients branch on them."""
    error = error_class()
    assert error.status_code == status_code
    assert error.code == code


def test_lockout_and_inactive_are_authentication_failures():
    """So one ``except AuthenticationError`` covers every failed-login path."""
    assert issubclass(AccountLockedError, AuthenticationError)
    assert issubclass(AccountInactiveError, AuthenticationError)


def test_insufficient_stock_is_a_conflict():
    assert issubclass(InsufficientStockError, ConflictError)


def test_a_custom_message_is_used_verbatim():
    assert NotFoundError("Shipment not found.").message == "Shipment not found."


def test_the_docstring_is_the_default_message():
    """Every exception is self-documenting rather than needing a message at each raise."""
    assert ConflictError().message == ConflictError.__doc__.strip()


def test_every_domain_error_is_catchable_as_one_type():
    for error_class in (NotFoundError, ConflictError, AuthenticationError, PermissionDeniedError):
        assert issubclass(error_class, DomainError)
