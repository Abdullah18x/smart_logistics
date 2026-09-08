"""The tables that govern the shipment lifecycle.

These are data, not code, which makes them easy to edit and easy to break. Each
test states an invariant that must survive future edits to the tables.
"""

from datetime import timedelta

import pytest

from app.core.enums import ServiceLevel, ShipmentStatus, UserRole
from app.core.shipment_state_machine import ALLOWED_TRANSITIONS, HOLDS_INVENTORY
from app.services.shipment_service import (
    EDITABLE_STATUSES,
    RELEASES_STOCK_ON_EXIT,
    SERVICE_LEVEL_WINDOWS,
    TRANSITION_ROLES,
)


class TestServiceLevelWindows:
    def test_every_service_level_has_a_delivery_promise(self):
        """An unmapped level would raise a KeyError while creating a shipment."""
        assert set(SERVICE_LEVEL_WINDOWS) == set(ServiceLevel)

    def test_promises_are_positive_durations(self):
        for level, window in SERVICE_LEVEL_WINDOWS.items():
            assert window > timedelta(0), f"{level} has a non-positive window"

    def test_faster_service_levels_promise_sooner(self):
        assert (
            SERVICE_LEVEL_WINDOWS[ServiceLevel.SAME_DAY]
            < SERVICE_LEVEL_WINDOWS[ServiceLevel.EXPRESS]
            < SERVICE_LEVEL_WINDOWS[ServiceLevel.STANDARD]
            < SERVICE_LEVEL_WINDOWS[ServiceLevel.ECONOMY]
        )


class TestTransitionRoles:
    def test_every_reachable_status_has_an_owning_role(self):
        """A status nobody is allowed to set could never be reached through the API."""
        reachable = {t for targets in ALLOWED_TRANSITIONS.values() for t in targets}
        assert reachable <= set(TRANSITION_ROLES)

    def test_created_needs_no_transition_role(self):
        """It is set by creation, not by a transition."""
        assert ShipmentStatus.CREATED not in TRANSITION_ROLES

    def test_admin_can_drive_every_transition(self):
        """Admins have to be able to unstick a shipment manually."""
        for status, roles in TRANSITION_ROLES.items():
            assert UserRole.ADMIN in roles, f"{status} cannot be driven by an admin"

    @pytest.mark.parametrize(
        "status",
        [
            ShipmentStatus.PICKED_UP,
            ShipmentStatus.IN_TRANSIT,
            ShipmentStatus.OUT_FOR_DELIVERY,
            ShipmentStatus.DELIVERED,
            ShipmentStatus.DELIVERY_FAILED,
        ],
    )
    def test_couriers_own_the_custody_states(self, status):
        assert UserRole.COURIER in TRANSITION_ROLES[status]

    @pytest.mark.parametrize(
        "status",
        [
            ShipmentStatus.READY_FOR_DISPATCH,
            ShipmentStatus.DISPATCHING,
            ShipmentStatus.DISPATCHED,
            ShipmentStatus.CANCELLED,
        ],
    )
    def test_couriers_do_not_own_warehouse_or_commercial_states(self, status):
        """A courier must not be able to mark stock ready, dispatched or cancelled."""
        assert UserRole.COURIER not in TRANSITION_ROLES[status]

    def test_support_can_cancel_and_start_returns(self):
        assert UserRole.CUSTOMER_SUPPORT in TRANSITION_ROLES[ShipmentStatus.CANCELLED]
        assert UserRole.CUSTOMER_SUPPORT in TRANSITION_ROLES[ShipmentStatus.RETURN_INITIATED]

    def test_only_admins_drive_the_dispatch_machinery(self):
        """Phase 2 hands these to the workflow engine; until then they stay locked down."""
        assert TRANSITION_ROLES[ShipmentStatus.DISPATCHING] == frozenset({UserRole.ADMIN})
        assert TRANSITION_ROLES[ShipmentStatus.DISPATCHED] == frozenset({UserRole.ADMIN})


class TestStockAndEditWindows:
    def test_stock_is_released_on_exit_from_every_state_that_holds_it(self):
        """Otherwise cancelling a shipment would strand its stock permanently."""
        assert HOLDS_INVENTORY <= RELEASES_STOCK_ON_EXIT

    def test_a_freshly_created_shipment_also_releases_stock(self):
        """Creation takes the hold, so CREATED must give it back too."""
        assert ShipmentStatus.CREATED in RELEASES_STOCK_ON_EXIT

    def test_nothing_in_courier_custody_releases_stock_on_cancellation(self):
        """Those units have physically left; the ledger already recorded them."""
        assert ShipmentStatus.PICKED_UP not in RELEASES_STOCK_ON_EXIT
        assert ShipmentStatus.DISPATCHED not in RELEASES_STOCK_ON_EXIT

    def test_shipments_are_only_editable_before_dispatch(self):
        """Editing contents afterwards would misrepresent what was sent."""
        post_dispatch = {
            ShipmentStatus.DISPATCHED,
            ShipmentStatus.PICKED_UP,
            ShipmentStatus.IN_TRANSIT,
            ShipmentStatus.OUT_FOR_DELIVERY,
            ShipmentStatus.DELIVERED,
            ShipmentStatus.RETURNED,
            ShipmentStatus.CANCELLED,
        }
        assert EDITABLE_STATUSES.isdisjoint(post_dispatch)
