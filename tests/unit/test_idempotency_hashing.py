"""Request-body hashing for idempotent writes.

The hash decides whether a repeated ``Idempotency-Key`` is a genuine retry or a
client bug. It must be blind to things that do not change meaning (key order)
and sensitive to everything that does.
"""

import uuid
from datetime import UTC, datetime

from app.services.idempotency_service import hash_payload


class TestStability:
    def test_key_order_does_not_matter(self):
        """JSON objects are unordered; a retry may serialise them differently."""
        assert hash_payload({"a": 1, "b": 2}) == hash_payload({"b": 2, "a": 1})

    def test_nested_key_order_does_not_matter(self):
        first = {"outer": {"a": 1, "b": [1, 2, {"x": 1, "y": 2}]}}
        second = {"outer": {"b": [1, 2, {"y": 2, "x": 1}], "a": 1}}
        assert hash_payload(first) == hash_payload(second)

    def test_the_same_payload_always_hashes_the_same(self):
        payload = {"origin_warehouse_id": str(uuid.uuid4()), "items": [{"quantity": 2}]}
        assert hash_payload(payload) == hash_payload(payload)

    def test_output_is_a_sha256_hex_digest(self):
        digest = hash_payload({"a": 1})
        assert len(digest) == 64
        assert int(digest, 16) >= 0


class TestSensitivity:
    def test_a_changed_value_changes_the_hash(self):
        assert hash_payload({"quantity": 2}) != hash_payload({"quantity": 3})

    def test_an_added_field_changes_the_hash(self):
        assert hash_payload({"a": 1}) != hash_payload({"a": 1, "b": 2})

    def test_list_order_is_significant(self):
        """Two items in a different order is a different shipment."""
        assert hash_payload([{"sku": "A"}, {"sku": "B"}]) != hash_payload(
            [{"sku": "B"}, {"sku": "A"}]
        )

    def test_a_number_and_its_string_differ(self):
        assert hash_payload({"quantity": 2}) != hash_payload({"quantity": "2"})

    def test_null_is_not_the_same_as_absent(self):
        assert hash_payload({"a": 1, "b": None}) != hash_payload({"a": 1})


class TestNonJsonTypes:
    def test_uuids_are_hashable(self):
        """Payloads reach this function before serialisation in some paths."""
        identifier = uuid.uuid4()
        assert hash_payload({"id": identifier}) == hash_payload({"id": identifier})

    def test_datetimes_are_hashable(self):
        moment = datetime.now(UTC)
        assert hash_payload({"at": moment}) == hash_payload({"at": moment})

    def test_scalars_and_empty_bodies_are_accepted(self):
        for payload in (None, "", 0, [], {}):
            assert len(hash_payload(payload)) == 64
