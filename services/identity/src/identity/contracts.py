"""Contracts for the events Identity publishes on ``identity.events``.

Every ``user.*`` event carries the user's public profile (never credentials or
lockout state), plus a few fields specific to the change.
"""

from identity.models import UserStatus
from sl_platform.contracts import boolean, enum, event, extend, nullable, register, string, uuid
from sl_platform.roles import UserRole

ROLES = enum(*(r.value for r in UserRole))
STATUSES = enum(*(s.value for s in UserStatus))

USER = event(
    "user",
    {
        "id": uuid(),
        "email": string(),
        "full_name": string(),
        "phone": nullable(string()),
        "role": ROLES,
        "status": STATUSES,
        "courier_id": nullable(uuid()),
        "deleted": boolean(),
        "actor_id": nullable(uuid()),
    },
    required=("id", "email", "full_name", "role", "status", "deleted"),
    description="A user's public profile after the change.",
)

CONTRACTS = {
    "user.created": extend(USER, "user.created"),
    "user.updated": extend(
        USER,
        "user.updated",
        linked_courier_id=uuid(),
        unlinked_courier_id=uuid(),
    ),
    "user.role_changed": extend(
        USER,
        "user.role_changed",
        required=("previous_role",),
        previous_role=ROLES,
        reason=nullable(string()),
    ),
    "user.status_changed": extend(
        USER,
        "user.status_changed",
        required=("previous_status",),
        previous_status=STATUSES,
        reason=nullable(string()),
    ),
    "user.deleted": extend(USER, "user.deleted"),
    "user.restored": extend(USER, "user.restored"),
    "user.scope_changed": extend(
        USER,
        "user.scope_changed",
        added_warehouse_id=uuid(),
        removed_warehouse_id=uuid(),
    ),
}

# Known to this process from import: add_event checks every payload against them.
register(CONTRACTS)
