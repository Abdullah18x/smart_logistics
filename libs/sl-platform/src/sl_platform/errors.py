"""Domain errors and their single translation point to HTTP.

Services raise these; ``install_error_handlers`` turns them into the shared
``Problem`` body. Errors returned by another service travel back through
``RemoteError`` with their original status and code, so a 409
``insufficient_stock`` from Inventory reaches the client as exactly that.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError


class DomainError(Exception):
    """Base class for expected, business-level failures."""

    status_code = 400
    code = "domain_error"

    def __init__(self, message: str | None = None) -> None:
        self.message = message or (self.__doc__ or "").strip()
        super().__init__(self.message)


class NotFoundError(DomainError):
    """The requested resource does not exist."""

    status_code = 404
    code = "not_found"


class ConflictError(DomainError):
    """The request conflicts with the current state of the resource."""

    status_code = 409
    code = "conflict"


class AuthenticationError(DomainError):
    """Invalid or missing credentials."""

    status_code = 401
    code = "authentication_failed"


class PermissionDeniedError(DomainError):
    """The caller is not allowed to perform this action."""

    status_code = 403
    code = "permission_denied"


class InvalidReferenceError(DomainError):
    """A referenced record does not exist, or is still referenced by others."""

    status_code = 409
    code = "invalid_reference"


class ConstraintViolationError(DomainError):
    """A value broke a database rule."""

    status_code = 422
    code = "constraint_violation"


class ConcurrentUpdateError(ConflictError):
    """Someone else changed this record first. Re-read it and try again."""

    code = "concurrent_update"


class UpstreamUnavailableError(DomainError):
    """A service this request depends on is unavailable. Retry shortly."""

    status_code = 503
    code = "upstream_unavailable"


class RemoteError(DomainError):
    """An error answered by another service, passed through unchanged."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


# --- Postgres constraint translation ----------------------------------------

UNIQUE_VIOLATION = "23505"
FOREIGN_KEY_VIOLATION = "23503"
CHECK_VIOLATION = "23514"
NOT_NULL_VIOLATION = "23502"


def _constraint_name(error: IntegrityError) -> str | None:
    original = getattr(error, "orig", None)
    for attribute in ("constraint_name", "constraint"):
        name = getattr(original, attribute, None)
        if name:
            return str(name)
    cause = getattr(original, "__cause__", None)
    name = getattr(cause, "constraint_name", None)
    return str(name) if name else None


def _sqlstate(error: IntegrityError) -> str | None:
    original = getattr(error, "orig", None)
    cause = getattr(original, "__cause__", original)
    return getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)


def translate_integrity_error(
    error: IntegrityError, messages: dict[str, str] | None = None
) -> DomainError:
    """Map a constraint violation to the domain error the API should return."""
    message = (messages or {}).get(_constraint_name(error) or "")
    code = _sqlstate(error)
    if code == UNIQUE_VIOLATION:
        return ConflictError(message or "That value is already in use.")
    if code == FOREIGN_KEY_VIOLATION:
        return InvalidReferenceError(message or "A referenced record does not exist.")
    if code in (CHECK_VIOLATION, NOT_NULL_VIOLATION):
        return ConstraintViolationError(message or "The request violates a data rule.")
    return ConflictError(message or "The request conflicts with existing data.")


def problem(error: DomainError) -> JSONResponse:
    headers = {"WWW-Authenticate": "Bearer"} if error.status_code == 401 else None
    return JSONResponse(
        status_code=error.status_code,
        content={"code": error.code, "message": error.message},
        headers=headers,
    )


def install_error_handlers(app: FastAPI, constraint_messages: dict[str, str] | None = None) -> None:
    @app.exception_handler(DomainError)
    async def _domain(_: Request, exc: DomainError) -> JSONResponse:
        return problem(exc)

    @app.exception_handler(IntegrityError)
    async def _integrity(_: Request, exc: IntegrityError) -> JSONResponse:
        return problem(translate_integrity_error(exc, constraint_messages))

    @app.exception_handler(StaleDataError)
    async def _stale(_: Request, __: StaleDataError) -> JSONResponse:
        # Raised by SQLAlchemy when a versioned UPDATE matches no row: another
        # request committed a change in between.
        return problem(ConcurrentUpdateError())

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "code": "validation_error",
                "message": "Request validation failed.",
                "detail": [
                    {"field": ".".join(str(p) for p in e["loc"][1:]), "error": e["msg"]}
                    for e in exc.errors()
                ],
            },
        )
