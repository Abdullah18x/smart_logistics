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


#: Roles that see every warehouse's rows.
UNRESTRICTED_ROLES = frozenset({UserRole.ADMIN, UserRole.CUSTOMER_SUPPORT})
