"""Domain exceptions.

Services raise these; the API layer translates them into HTTP responses, so no
module below ``controllers`` needs to know about status codes.
"""


class DomainError(Exception):
    """Base class for expected, business-level failures."""

    status_code = 400
    code = "domain_error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.__doc__)
        self.message = message or (self.__doc__ or "").strip()


class NotFoundError(DomainError):
    """The requested resource does not exist."""

    status_code = 404
    code = "not_found"


class ConflictError(DomainError):
    """The request conflicts with the current state of the resource."""

    status_code = 409
    code = "conflict"


class AuthenticationError(DomainError):
    """Invalid credentials."""

    status_code = 401
    code = "authentication_failed"


class PermissionDeniedError(DomainError):
    """The caller is not allowed to perform this action."""

    status_code = 403
    code = "permission_denied"


class AccountLockedError(AuthenticationError):
    """The account is temporarily locked after too many failed attempts."""

    code = "account_locked"


class AccountInactiveError(AuthenticationError):
    """The account is not active."""

    code = "account_inactive"
