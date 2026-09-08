"""Row-level access scoping (ADR-009).

The distinction that matters here is ``None`` versus ``[]``: unrestricted
versus restricted-to-nothing. Getting it backwards would either hide every row
from an admin or show every row to an unassigned operator.
"""

import dataclasses
import uuid

import pytest

from app.constants.enums import UserRole
from app.core.access import AccessScope

WAREHOUSE_A = uuid.uuid4()
WAREHOUSE_B = uuid.uuid4()


def scope(role: UserRole, **kwargs) -> AccessScope:
    return AccessScope(role=role, user_id=uuid.uuid4(), **kwargs)


class TestIsAdmin:
    def test_true_for_admin(self):
        assert scope(UserRole.ADMIN).is_admin is True

    @pytest.mark.parametrize(
        "role", [UserRole.CUSTOMER_SUPPORT, UserRole.WAREHOUSE_OPERATOR, UserRole.COURIER]
    )
    def test_false_for_everyone_else(self, role):
        assert scope(role).is_admin is False


class TestWarehouseScoping:
    def test_none_means_unrestricted(self):
        unrestricted = scope(UserRole.ADMIN)
        assert unrestricted.sees_all_warehouses is True
        assert unrestricted.may_touch_warehouse(WAREHOUSE_A) is True

    def test_a_list_restricts_to_those_warehouses(self):
        operator = scope(UserRole.WAREHOUSE_OPERATOR, warehouse_ids=[WAREHOUSE_A])
        assert operator.sees_all_warehouses is False
        assert operator.may_touch_warehouse(WAREHOUSE_A) is True
        assert operator.may_touch_warehouse(WAREHOUSE_B) is False

    def test_an_empty_list_grants_nothing(self):
        """An operator with no assignments sees nothing — not everything."""
        unassigned = scope(UserRole.WAREHOUSE_OPERATOR, warehouse_ids=[])
        assert unassigned.sees_all_warehouses is False
        assert unassigned.may_touch_warehouse(WAREHOUSE_A) is False

    def test_multiple_assignments_are_all_honoured(self):
        operator = scope(UserRole.WAREHOUSE_OPERATOR, warehouse_ids=[WAREHOUSE_A, WAREHOUSE_B])
        assert operator.may_touch_warehouse(WAREHOUSE_A)
        assert operator.may_touch_warehouse(WAREHOUSE_B)
        assert not operator.may_touch_warehouse(uuid.uuid4())


class TestCourierScoping:
    def test_courier_id_defaults_to_none(self):
        assert scope(UserRole.ADMIN).courier_id is None

    def test_courier_carries_its_fleet_profile_id(self):
        courier_id = uuid.uuid4()
        assert scope(UserRole.COURIER, courier_id=courier_id).courier_id == courier_id


class TestImmutability:
    def test_a_scope_cannot_be_mutated_after_it_is_built(self):
        """Built once per request; letting a service widen it would defeat the point."""
        built = scope(UserRole.COURIER)
        with pytest.raises(dataclasses.FrozenInstanceError):
            built.role = UserRole.ADMIN  # type: ignore[misc]
