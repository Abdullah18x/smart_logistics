"""Password and token hashing."""

import re

from app.core.security import (
    generate_token,
    hash_password,
    hash_token,
    needs_rehash,
    verify_password,
)

PASSWORD = "Correct!Horse2026"


class TestPasswordHashing:
    def test_hash_is_argon2id(self):
        assert hash_password(PASSWORD).startswith("$argon2id$")

    def test_hash_does_not_contain_the_password(self):
        assert PASSWORD not in hash_password(PASSWORD)

    def test_same_password_hashes_differently_each_time(self):
        """Distinct salts: two users with the same password must not collide."""
        assert hash_password(PASSWORD) != hash_password(PASSWORD)

    def test_verify_accepts_the_right_password(self):
        assert verify_password(PASSWORD, hash_password(PASSWORD)) is True

    def test_verify_rejects_the_wrong_password(self):
        assert verify_password("Wrong!Password2026", hash_password(PASSWORD)) is False

    def test_verify_is_case_sensitive(self):
        assert verify_password(PASSWORD.lower(), hash_password(PASSWORD)) is False

    def test_verify_returns_false_for_a_malformed_hash(self):
        """A corrupt stored hash must fail the login, not raise a 500."""
        assert verify_password(PASSWORD, "not-a-hash") is False

    def test_verify_returns_false_for_an_empty_hash(self):
        assert verify_password(PASSWORD, "") is False

    def test_a_fresh_hash_does_not_need_rehashing(self):
        assert needs_rehash(hash_password(PASSWORD)) is False


class TestTokenGeneration:
    def test_tokens_are_url_safe(self):
        assert re.fullmatch(r"[A-Za-z0-9_-]+", generate_token())

    def test_tokens_are_long_enough_to_be_unguessable(self):
        # 48 random bytes, base64url encoded.
        assert len(generate_token()) >= 60

    def test_tokens_are_unique(self):
        assert len({generate_token() for _ in range(200)}) == 200

    def test_length_is_configurable(self):
        assert len(generate_token(16)) < len(generate_token(64))


class TestTokenHashing:
    def test_hash_is_sha256_hex(self):
        assert re.fullmatch(r"[0-9a-f]{64}", hash_token("some-token"))

    def test_hashing_is_deterministic(self):
        """Lookup by hash only works if the same token always hashes the same."""
        assert hash_token("some-token") == hash_token("some-token")

    def test_different_tokens_hash_differently(self):
        assert hash_token("token-a") != hash_token("token-b")

    def test_hash_does_not_contain_the_token(self):
        assert "some-token" not in hash_token("some-token")
