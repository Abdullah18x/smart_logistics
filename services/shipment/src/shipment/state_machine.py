"""The single declaration of legal shipment transitions and who may make them."""

from shipment.models import ShipmentStatus as S
from sl_platform.errors import ConflictError
from sl_platform.roles import UserRole as R

#: status -> statuses reachable from it. An empty set marks a terminal state.
#: Cancellation is only possible before dispatch: once goods have left the
#: warehouse the stock is committed, so getting them back is a return, not a
#: cancel (the monolith allowed DISPATCHED -> CANCELLED and lost the stock).
ALLOWED_TRANSITIONS: dict[S, frozenset[S]] = {
    S.CREATED: frozenset({S.READY_FOR_DISPATCH, S.CANCELLED}),
    S.READY_FOR_DISPATCH: frozenset({S.DISPATCHING, S.CANCELLED}),
    S.DISPATCHING: frozenset({S.DISPATCHED, S.DISPATCH_FAILED}),
    S.DISPATCH_FAILED: frozenset({S.READY_FOR_DISPATCH, S.CANCELLED}),
    S.DISPATCHED: frozenset({S.PICKED_UP}),
    S.PICKED_UP: frozenset({S.IN_TRANSIT}),
    S.IN_TRANSIT: frozenset({S.OUT_FOR_DELIVERY}),
    S.OUT_FOR_DELIVERY: frozenset({S.DELIVERED, S.DELIVERY_FAILED}),
    S.DELIVERY_FAILED: frozenset({S.OUT_FOR_DELIVERY, S.RETURN_INITIATED}),
    S.RETURN_INITIATED: frozenset({S.RETURNED}),
    S.DELIVERED: frozenset(),
    S.RETURNED: frozenset(),
    S.CANCELLED: frozenset(),
}

TERMINAL_STATES = frozenset(s for s, nxt in ALLOWED_TRANSITIONS.items() if not nxt)

#: Contents may change only before anything has left the warehouse.
EDITABLE_STATES = frozenset({S.CREATED, S.READY_FOR_DISPATCH, S.DISPATCH_FAILED})

#: Statuses still owed to the customer (for the "overdue" filter).
OPEN_STATES = frozenset(set(S) - TERMINAL_STATES - {S.RETURN_INITIATED})

#: Which role may move a shipment *into* each status. The dispatch and
#: delivery workflows (Temporal, Phase 2) take over the middle of this table.
TRANSITION_ROLES: dict[S, frozenset[R]] = {
    S.READY_FOR_DISPATCH: frozenset({R.ADMIN, R.WAREHOUSE_OPERATOR}),
    S.DISPATCHING: frozenset({R.ADMIN}),
    S.DISPATCHED: frozenset({R.ADMIN}),
    S.DISPATCH_FAILED: frozenset({R.ADMIN}),
    S.PICKED_UP: frozenset({R.ADMIN, R.COURIER}),
    S.IN_TRANSIT: frozenset({R.ADMIN, R.COURIER}),
    S.OUT_FOR_DELIVERY: frozenset({R.ADMIN, R.COURIER}),
    S.DELIVERED: frozenset({R.ADMIN, R.COURIER}),
    S.DELIVERY_FAILED: frozenset({R.ADMIN, R.COURIER}),
    S.RETURN_INITIATED: frozenset({R.ADMIN, R.CUSTOMER_SUPPORT}),
    S.RETURNED: frozenset({R.ADMIN, R.WAREHOUSE_OPERATOR}),
    S.CANCELLED: frozenset({R.ADMIN, R.CUSTOMER_SUPPORT}),
}


def can_transition(current: S, target: S) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


def assert_transition(current: S, target: S) -> None:
    if current == target:
        raise ConflictError(f"Shipment is already {target.value}.")
    if not can_transition(current, target):
        allowed = sorted(s.value for s in ALLOWED_TRANSITIONS.get(current, frozenset()))
        raise ConflictError(
            f"Cannot move a shipment from {current.value} to {target.value}. "
            + (f"Allowed: {', '.join(allowed)}." if allowed else f"{current.value} is terminal.")
        )
