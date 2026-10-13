"""The role vocabulary shared by every service.

Roles travel inside the access token, so all services must agree on the values.
This is the one domain enum the platform owns; everything else (shipment
statuses, warehouse types) belongs to the service that owns the data.
"""

from enum import StrEnum


class UserRole(StrEnum):
    ADMIN = "admin"
    CUSTOMER_SUPPORT = "customer_support"
    WAREHOUSE_OPERATOR = "warehouse_operator"
    COURIER = "courier"
    #: A service account (the orchestrator), never a person. Identity issues these
    #: tokens from client credentials; no user row can hold this role.
    SYSTEM = "system"


#: Roles a person can have. ``SYSTEM`` is excluded: the identity database's
#: ``user_role`` enum does not even contain it.
HUMAN_ROLES = frozenset(
    {UserRole.ADMIN, UserRole.CUSTOMER_SUPPORT, UserRole.WAREHOUSE_OPERATOR, UserRole.COURIER}
)

#: Roles that see every warehouse's rows.
UNRESTRICTED_ROLES = frozenset({UserRole.ADMIN, UserRole.CUSTOMER_SUPPORT, UserRole.SYSTEM})
