"""User and authentication contracts.

Validation at the boundary is the cheapest place to reject bad input, and the
password rules here are the only thing stopping a weak credential reaching the
hasher.
"""

import pytest
from pydantic import ValidationError

from app.core.enums import UserRole, UserStatus
from app.schemas.auth import LoginRequest, PasswordResetRequest
from app.schemas.user import (
    PASSWORD_MIN_LENGTH,
    PasswordChange,
    UserCreate,
    UserUpdate,
    validate_password_strength,
)

VALID = {
    "email": "ops@transfleet.com",
    "full_name": "Ayesha Khan",
    "password": "Dispatch!2026Ops",
    "role": UserRole.WAREHOUSE_OPERATOR,
}


class TestPasswordStrength:
    def test_a_strong_password_is_accepted(self):
        assert validate_password_strength("Dispatch!2026Ops") == "Dispatch!2026Ops"

    @pytest.mark.parametrize(
        ("password", "missing"),
        [
            ("dispatch!2026ops", "an uppercase letter"),
            ("DISPATCH!2026OPS", "a lowercase letter"),
            ("DispatchOps!Word", "a digit"),
            ("Dispatch2026Ops1", "a symbol"),
        ],
    )
    def test_each_missing_character_class_is_named(self, password, missing):
        """The client is told what is wrong, not just that something is."""
        with pytest.raises(ValueError, match=missing):
            validate_password_strength(password)

    def test_a_short_password_is_rejected_by_length_before_content(self):
        with pytest.raises(ValidationError) as exc:
            UserCreate(**VALID | {"password": "Ab1!efg"})
        assert "at least" in str(exc.value).lower()

    def test_the_minimum_length_is_twelve(self):
        assert PASSWORD_MIN_LENGTH == 12
        UserCreate(**VALID | {"password": "Abcdefg1!hij"})  # exactly 12, valid
        with pytest.raises(ValidationError):
            UserCreate(**VALID | {"password": "Abcdef1!hij"})  # 11

    def test_an_over_long_password_is_rejected(self):
        """Argon2 on unbounded input is a denial-of-service vector."""
        with pytest.raises(ValidationError):
            UserCreate(**VALID | {"password": "Aa1!" + "x" * 200})

    def test_password_change_enforces_the_same_rules(self):
        with pytest.raises(ValidationError):
            PasswordChange(current_password="whatever", new_password="weak")
        assert PasswordChange(current_password="whatever", new_password="Dispatch!2026Ops")


class TestNormalisation:
    def test_email_is_lowercased_and_trimmed(self):
        user = UserCreate(**VALID | {"email": "  OPS@TransFleet.COM  "})
        assert user.email == "ops@transfleet.com"

    def test_login_email_is_normalised_the_same_way(self):
        """Otherwise a user who types their address in capitals cannot log in."""
        assert LoginRequest(email="  Admin@TransFleet.com ", password="x").email == (
            "admin@transfleet.com"
        )

    def test_password_reset_email_is_normalised(self):
        assert PasswordResetRequest(email="ME@Example.COM").email == "me@example.com"

    def test_name_whitespace_is_collapsed(self):
        assert UserCreate(**VALID | {"full_name": "  Ayesha   Khan  "}).full_name == "Ayesha Khan"

    def test_a_malformed_email_is_rejected(self):
        with pytest.raises(ValidationError):
            UserCreate(**VALID | {"email": "not-an-email"})


class TestPhone:
    @pytest.mark.parametrize(
        "phone", ["+923001234567", "0300 123 4567", "(021) 111-2222", "+92-21-1112222"]
    )
    def test_common_formats_are_accepted(self, phone):
        assert UserCreate(**VALID | {"phone": phone}).phone == phone

    @pytest.mark.parametrize("phone", ["not-a-phone", "123", "+92300123456789012345678901234567"])
    def test_junk_is_rejected(self, phone):
        with pytest.raises(ValidationError):
            UserCreate(**VALID | {"phone": phone})

    def test_phone_is_optional(self):
        assert UserCreate(**VALID).phone is None


class TestPrivilegeFieldsAreNotSelfService:
    def test_profile_updates_cannot_carry_a_role(self):
        """Role changes have their own admin-only endpoint; this is the guard."""
        assert "role" not in UserUpdate.model_fields

    def test_profile_updates_cannot_carry_a_status(self):
        assert "status" not in UserUpdate.model_fields

    def test_a_role_sent_to_a_profile_update_is_ignored(self):
        update = UserUpdate.model_validate({"full_name": "New Name", "role": "admin"})
        assert not hasattr(update, "role")

    def test_creating_a_user_requires_an_explicit_role(self):
        """No default: an accidental admin is worse than a rejected request."""
        with pytest.raises(ValidationError):
            UserCreate(**{k: v for k, v in VALID.items() if k != "role"})

    @pytest.mark.parametrize("role", list(UserRole))
    def test_every_role_can_be_requested(self, role):
        assert UserCreate(**VALID | {"role": role}).role is role

    def test_an_unknown_role_is_rejected(self):
        with pytest.raises(ValidationError):
            UserCreate(**VALID | {"role": "superuser"})

    def test_an_unknown_status_is_rejected(self):
        from app.schemas.user import UserStatusUpdate

        assert UserStatusUpdate(status=UserStatus.SUSPENDED).status is UserStatus.SUSPENDED
        with pytest.raises(ValidationError):
            UserStatusUpdate(status="banned")
