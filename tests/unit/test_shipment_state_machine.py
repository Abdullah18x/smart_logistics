"""The shipment lifecycle is declared in exactly one place; these tests hold it there.

A transition that is not in ``ALLOWED_TRANSITIONS`` must be impossible
everywhere in the codebase, so the map itself is worth asserting on as data —
not just the behaviour of the two functions that read it.
"""

import pytest

from app.core.enums import ShipmentStatus as S
from app.core.exceptions import ConflictError
from app.core.shipment_state_machine import (
    ALLOWED_TRANSITIONS,
    HOLDS_INVENTORY,
    IN_COURIER_CUSTODY,
    TERMINAL_STATES,
    assert_transition,
    can_transition,
)


class TestTransitionMap:
    def test_every_status_is_declared(self):
        """A status missing from the map would silently become a dead end."""
        assert set(ALLOWED_TRANSITIONS) == set(S)

    def test_every_target_is_a_real_status(self):
        for source, targets in ALLOWED_TRANSITIONS.items():
            assert set(targets) <= set(S), f"{source} points at an unknown status"

    def test_no_status_transitions_to_itself(self):
        for source, targets in ALLOWED_TRANSITIONS.items():
            assert source not in targets

    def test_terminal_states_are_the_three_end_states(self):
        assert frozenset({S.DELIVERED, S.RETURNED, S.CANCELLED}) == TERMINAL_STATES

    def test_created_is_the_only_entry_point(self):
        """Nothing may move back to CREATED — it is where a shipment starts."""
        reachable = {t for targets in ALLOWED_TRANSITIONS.values() for t in targets}
        assert S.CREATED not in reachable

    def test_every_status_is_reachable_from_created(self):
        """A state nobody can reach is dead code in the lifecycle."""
        seen = {S.CREATED}
        frontier = [S.CREATED]
        while frontier:
            for target in ALLOWED_TRANSITIONS[frontier.pop()]:
                if target not in seen:
                    seen.add(target)
                    frontier.append(target)
        assert seen == set(S)

    def test_delivered_is_final(self):
        assert ALLOWED_TRANSITIONS[S.DELIVERED] == frozenset()

    def test_cancellation_is_impossible_once_a_courier_has_custody(self):
        """After pickup the parcel is physically gone; cancelling is a return."""
        for status in IN_COURIER_CUSTODY:
            assert S.CANCELLED not in ALLOWED_TRANSITIONS[status]

    def test_held_stock_can_never_be_stranded(self):
        """Every state that holds stock must be able to reach one that resolves it.

        Resolution is either DISPATCHED (the hold becomes a real reduction) or
        CANCELLED (the hold is given back). DISPATCHING cannot be cancelled
        directly — the saga has to finish first — but it always lands on
        DISPATCHED or DISPATCH_FAILED, and the latter can be cancelled.
        """
        resolving = {S.DISPATCHED, S.CANCELLED}
        for status in HOLDS_INVENTORY:
            seen, frontier = {status}, [status]
            while frontier and not (seen & resolving):
                for target in ALLOWED_TRANSITIONS[frontier.pop()]:
                    if target not in seen:
                        seen.add(target)
                        frontier.append(target)
            assert seen & resolving, f"stock held in {status} can never be resolved"

    def test_failed_dispatch_is_retryable(self):
        assert S.READY_FOR_DISPATCH in ALLOWED_TRANSITIONS[S.DISPATCH_FAILED]

    def test_failed_delivery_can_be_reattempted_or_returned(self):
        assert ALLOWED_TRANSITIONS[S.DELIVERY_FAILED] == frozenset(
            {S.OUT_FOR_DELIVERY, S.RETURN_INITIATED}
        )


class TestCanTransition:
    @pytest.mark.parametrize(
        ("current", "target"),
        [
            (S.CREATED, S.READY_FOR_DISPATCH),
            (S.CREATED, S.CANCELLED),
            (S.READY_FOR_DISPATCH, S.DISPATCHING),
            (S.DISPATCHING, S.DISPATCHED),
            (S.DISPATCHED, S.PICKED_UP),
            (S.PICKED_UP, S.IN_TRANSIT),
            (S.IN_TRANSIT, S.OUT_FOR_DELIVERY),
            (S.OUT_FOR_DELIVERY, S.DELIVERED),
            (S.RETURN_INITIATED, S.RETURNED),
        ],
    )
    def test_legal_transitions(self, current, target):
        assert can_transition(current, target) is True

    @pytest.mark.parametrize(
        ("current", "target"),
        [
            (S.CREATED, S.DELIVERED),
            (S.CREATED, S.DISPATCHED),
            (S.DELIVERED, S.CANCELLED),
            (S.CANCELLED, S.CREATED),
            (S.RETURNED, S.IN_TRANSIT),
            (S.IN_TRANSIT, S.DELIVERED),
            (S.PICKED_UP, S.CANCELLED),
        ],
    )
    def test_illegal_transitions(self, current, target):
        assert can_transition(current, target) is False


class TestAssertTransition:
    def test_legal_transition_is_silent(self):
        assert assert_transition(S.CREATED, S.READY_FOR_DISPATCH) is None

    def test_illegal_transition_raises_conflict(self):
        with pytest.raises(ConflictError) as exc:
            assert_transition(S.CREATED, S.DELIVERED)
        assert exc.value.status_code == 409
        assert exc.value.code == "conflict"

    def test_illegal_transition_names_what_is_allowed(self):
        """The client must be told what it could have done instead."""
        with pytest.raises(ConflictError) as exc:
            assert_transition(S.CREATED, S.DELIVERED)
        message = str(exc.value)
        assert "created" in message and "delivered" in message
        assert "ready_for_dispatch" in message and "cancelled" in message

    def test_leaving_a_terminal_state_says_so(self):
        with pytest.raises(ConflictError) as exc:
            assert_transition(S.DELIVERED, S.IN_TRANSIT)
        assert "terminal" in str(exc.value)

    def test_repeating_the_current_status_is_a_conflict(self):
        """A no-op transition is a client mistake, not a success."""
        with pytest.raises(ConflictError) as exc:
            assert_transition(S.IN_TRANSIT, S.IN_TRANSIT)
        assert "already in_transit" in str(exc.value)
