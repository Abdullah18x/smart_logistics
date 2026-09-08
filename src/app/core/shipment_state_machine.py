"""The single declaration of legal shipment status transitions.

Every status change in the system is validated here. Keeping the map in one
place is what makes the lifecycle auditable — a transition that is not listed
cannot happen anywhere in the codebase.
"""

from app.core.enums import ShipmentStatus as S
from app.core.exceptions import ConflictError

#: status -> statuses reachable from it. An empty set marks a terminal state.
ALLOWED_TRANSITIONS: dict[S, frozenset[S]] = {
    S.CREATED: frozenset({S.READY_FOR_DISPATCH, S.CANCELLED}),
    S.READY_FOR_DISPATCH: frozenset({S.DISPATCHING, S.CANCELLED}),
    S.DISPATCHING: frozenset({S.DISPATCHED, S.DISPATCH_FAILED}),
    # A failed dispatch is retryable: the saga has already compensated, so the
    # shipment is back to a clean, packed state.
    S.DISPATCH_FAILED: frozenset({S.READY_FOR_DISPATCH, S.CANCELLED}),
    S.DISPATCHED: frozenset({S.PICKED_UP, S.CANCELLED}),
    S.PICKED_UP: frozenset({S.IN_TRANSIT}),
    S.IN_TRANSIT: frozenset({S.OUT_FOR_DELIVERY}),
    S.OUT_FOR_DELIVERY: frozenset({S.DELIVERED, S.DELIVERY_FAILED}),
    S.DELIVERY_FAILED: frozenset({S.OUT_FOR_DELIVERY, S.RETURN_INITIATED}),
    S.RETURN_INITIATED: frozenset({S.RETURNED}),
    S.DELIVERED: frozenset(),
    S.RETURNED: frozenset(),
    S.CANCELLED: frozenset(),
}

#: States from which stock is still held and must be released on cancellation.
HOLDS_INVENTORY: frozenset[S] = frozenset({S.READY_FOR_DISPATCH, S.DISPATCHING, S.DISPATCH_FAILED})

#: States in which the shipment is physically with a courier.
IN_COURIER_CUSTODY: frozenset[S] = frozenset(
    {S.PICKED_UP, S.IN_TRANSIT, S.OUT_FOR_DELIVERY, S.DELIVERY_FAILED}
)

TERMINAL_STATES: frozenset[S] = frozenset(
    status for status, nxt in ALLOWED_TRANSITIONS.items() if not nxt
)


def can_transition(current: S, target: S) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


def assert_transition(current: S, target: S) -> None:
    """Raise ``ConflictError`` (HTTP 409) if the transition is not permitted.

    An illegal transition is never applied silently — a shipment reaching an
    impossible state is a data-integrity failure, not a warning.
    """
    if current == target:
        raise ConflictError(f"Shipment is already {target.value}.")
    if not can_transition(current, target):
        allowed = sorted(s.value for s in ALLOWED_TRANSITIONS.get(current, frozenset()))
        raise ConflictError(
            f"Cannot move a shipment from {current.value} to {target.value}. "
            + (f"Allowed: {', '.join(allowed)}." if allowed else f"{current.value} is terminal.")
        )
