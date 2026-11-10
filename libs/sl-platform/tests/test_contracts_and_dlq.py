"""Event contracts, the Schema Registry client, and dead letters. No broker needed:
the registry is an in-memory fake speaking its REST API."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from prometheus_client import REGISTRY

from sl_platform import contracts
from sl_platform.consumer import KafkaConsumerRunner, StatelessProcessor, receive
from sl_platform.dlq import DeadLetters, collapse
from sl_platform.events import EventEnvelope, Topics
from sl_platform.outbox import add_event, publish_envelope
from sl_platform.schema_registry import (
    IncompatibleSchemaError,
    RegistryError,
    SchemaRegistry,
    subject_for,
)

ORDER = contracts.event(
    "order.placed",
    {
        "id": contracts.uuid(),
        "qty": contracts.integer(minimum=1),
        "note": contracts.nullable(contracts.string()),
        "channel": contracts.nullable(contracts.enum("web", "app")),
    },
    required=("id", "qty"),
)
contracts.register({"order.placed": ORDER})


def sample(name: str, labels: dict[str, str]) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


class FakeRegistry:
    """Confluent's REST API, in memory: subjects, versions, ids, BACKWARD checks
    reduced to 'a new required property breaks old data'."""

    def __init__(self) -> None:
        self.schemas: dict[int, str] = {}
        self.subjects: dict[str, list[int]] = {}
        self.config: dict[str, str] = {}
        self.calls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(f"{request.method} {request.url.path}")
        path = request.url.path
        if request.method == "PUT" and path.startswith("/config/"):
            self.config[path.removeprefix("/config/")] = json.loads(request.content)[
                "compatibility"
            ]
            return httpx.Response(200, json={"compatibility": "BACKWARD"})
        if request.method == "POST" and path.endswith("/versions"):
            subject = path.split("/")[2]
            schema = json.loads(request.content)["schema"]
            for schema_id, text in self.schemas.items():
                if text == schema and schema_id in self.subjects.get(subject, []):
                    return httpx.Response(200, json={"id": schema_id})
            versions = self.subjects.setdefault(subject, [])
            if versions:
                old = json.loads(self.schemas[versions[-1]])
                if set(json.loads(schema)["required"]) - set(old["required"]):
                    return httpx.Response(409, json={"message": "incompatible"})
            schema_id = len(self.schemas) + 1
            self.schemas[schema_id] = schema
            versions.append(schema_id)
            return httpx.Response(200, json={"id": schema_id})
        if request.method == "GET" and path.startswith("/schemas/ids/"):
            schema_id = int(path.rsplit("/", 1)[1])
            if schema_id not in self.schemas:
                return httpx.Response(404, json={"message": "not found"})
            return httpx.Response(200, json={"schema": self.schemas[schema_id]})
        return httpx.Response(404)

    def client(self) -> SchemaRegistry:
        return SchemaRegistry("http://registry", transport=httpx.MockTransport(self.handler))


class TestContracts:
    def test_a_valid_payload_passes_and_extra_fields_are_allowed(self):
        contracts.validate("order.placed", {"id": str(uuid.uuid4()), "qty": 2, "extra": True})

    def test_a_broken_payload_says_where(self):
        with pytest.raises(contracts.ContractError, match="qty: 0 is less than the minimum"):
            contracts.validate("order.placed", {"id": str(uuid.uuid4()), "qty": 0})

    def test_nullable_enums_take_null_but_not_strangers(self):
        ok = {"id": str(uuid.uuid4()), "qty": 1, "channel": None}
        contracts.validate("order.placed", ok)
        with pytest.raises(contracts.ContractError, match="channel"):
            contracts.validate("order.placed", {**ok, "channel": "fax"})

    def test_an_event_type_without_a_contract_is_refused(self):
        with pytest.raises(contracts.ContractError, match="No contract"):
            contracts.validate("order.vanished", {})

    def test_extend_adds_fields_and_requirements_without_touching_the_base(self):
        cancelled = contracts.extend(
            ORDER, "order.cancelled", required=("reason",), reason=contracts.string()
        )
        assert "reason" in cancelled["required"] and "reason" not in ORDER["required"]
        assert cancelled["title"] == "order.cancelled" and ORDER["title"] == "order.placed"

    def test_staging_an_event_checks_its_contract_so_the_change_rolls_back(self):
        class Session:
            def __init__(self) -> None:
                self.added: list = []

            def add(self, row):
                self.added.append(row)

        session = Session()
        with pytest.raises(contracts.ContractError):
            add_event(session, topic="t", event_type="order.placed", key="k",
                      payload={"id": "not-a-uuid", "qty": 1}, producer="p")  # fmt: skip
        assert session.added == []
        add_event(session, topic="t", event_type="order.placed", key="k",
                  payload={"id": uuid.uuid4(), "qty": 1}, producer="p")  # fmt: skip
        assert len(session.added) == 1


class TestSchemaRegistry:
    async def test_registration_sets_backward_compatibility_and_is_cached(self):
        fake = FakeRegistry()
        registry = fake.client()
        first = await registry.register("orders-order.placed", ORDER)
        again = await registry.register("orders-order.placed", ORDER)
        assert first == again
        assert fake.config["orders-order.placed"] == "BACKWARD"
        assert fake.calls.count("POST /subjects/orders-order.placed/versions") == 1

    async def test_a_breaking_change_is_refused(self):
        fake = FakeRegistry()
        registry = fake.client()
        await registry.register("s", ORDER)
        stricter = contracts.extend(ORDER, "order.placed", required=("note",))
        with pytest.raises(IncompatibleSchemaError, match="incompatible"):
            await registry.register("s", stricter)

    async def test_schemas_are_fetched_by_id_once(self):
        fake = FakeRegistry()
        schema_id = await fake.client().register("s", ORDER)
        reader = fake.client()
        assert await reader.schema(schema_id) == ORDER
        await reader.schema(schema_id)
        assert fake.calls.count(f"GET /schemas/ids/{schema_id}") == 1

    async def test_an_unreachable_registry_is_a_registry_error(self):
        def down(_):
            raise httpx.ConnectError("refused")

        registry = SchemaRegistry("http://registry", transport=httpx.MockTransport(down))
        with pytest.raises(RegistryError, match="unreachable"):
            await registry.register("s", ORDER)

    async def test_the_relay_stamps_the_schema_id_when_it_publishes(self):
        fake = FakeRegistry()
        sent = []

        class Publisher:
            async def send(self, topic, key, value, headers=None):
                sent.append(json.loads(value))

        envelope = EventEnvelope(
            event_type="order.placed", producer="p", key="k",
            payload={"id": str(uuid.uuid4()), "qty": 1},
        ).model_dump(mode="json")  # fmt: skip
        await publish_envelope(Publisher(), "orders", envelope, fake.client())
        assert sent[0]["schema_id"] == fake.subjects[subject_for("orders", "order.placed")][0]


class RecordingPublisher:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, bytes, dict]] = []

    async def send(self, topic, key, value, headers=None):
        self.sent.append((topic, key, value, headers or {}))


def raw_event(payload: dict, *, schema_id: int | None = None) -> bytes:
    return EventEnvelope(
        event_type="order.placed", producer="p", key="k", payload=payload, schema_id=schema_id
    ).to_bytes()


class TestReceive:
    def consumer(self, handler) -> StatelessProcessor:
        return StatelessProcessor(
            f"c-{uuid.uuid4().hex[:6]}", {"order.placed": handler}, ("orders",)
        )

    async def test_an_unreadable_message_goes_to_the_dead_letter_topic(self):
        publisher = RecordingPublisher()
        processor = self.consumer(lambda e: None)
        outcome = await receive(processor, b"{not json", topic="orders", key="k",
                                dead_letters=DeadLetters(publisher))  # fmt: skip
        assert outcome == "dead_lettered"
        topic, _, value, headers = publisher.sent[0]
        assert topic == f"dlq.{processor.name}" and value == b"{not json"
        assert headers["dlq.reason"] == "unparseable" and headers["dlq.source-topic"] == "orders"

    async def test_a_payload_that_breaks_its_registered_schema_is_never_handled(self):
        fake = FakeRegistry()
        schema_id = await fake.client().register("orders-order.placed", ORDER)
        handled, publisher = [], RecordingPublisher()

        async def handler(envelope):
            handled.append(envelope)

        processor = self.consumer(handler)
        outcome = await receive(
            processor, raw_event({"id": "nope", "qty": 1}, schema_id=schema_id),
            registry=fake.client(), dead_letters=DeadLetters(publisher),
        )  # fmt: skip
        assert outcome == "dead_lettered" and handled == []
        assert publisher.sent[0][3]["dlq.reason"] == "contract_violation"
        labels = {"consumer": processor.name, "reason": "contract_violation"}
        assert sample("dead_letters_total", labels) == 1

    async def test_a_handler_that_keeps_failing_is_retried_then_dead_lettered(self):
        calls, publisher = [], RecordingPublisher()

        async def broken(_):
            calls.append(1)
            raise RuntimeError("database says no")

        processor = self.consumer(broken)
        outcome = await receive(
            processor, raw_event({"id": str(uuid.uuid4()), "qty": 1}),
            dead_letters=DeadLetters(publisher), max_attempts=3, backoff=lambda _: 0,
        )  # fmt: skip
        assert outcome == "dead_lettered" and len(calls) == 3
        headers = publisher.sent[0][3]
        assert headers["dlq.reason"] == "handler_failed" and headers["dlq.attempts"] == "3"
        assert "database says no" in headers["dlq.error"]
        assert headers["dlq.event-type"] == "order.placed"

    async def test_a_registry_outage_never_blocks_consumption(self):
        def down(_):
            raise httpx.ConnectError("refused")

        handled = []

        async def handler(envelope):
            handled.append(envelope)

        processor = self.consumer(handler)
        registry = SchemaRegistry("http://registry", transport=httpx.MockTransport(down))
        outcome = await receive(
            processor, raw_event({"id": str(uuid.uuid4()), "qty": 1}, schema_id=7),
            registry=registry,
        )  # fmt: skip
        assert outcome == "processed" and len(handled) == 1
        labels = {"consumer": processor.name, "reason": "registry_unavailable"}
        assert sample("events_unvalidated_total", labels) == 1

    def test_each_consumer_also_reads_its_own_retry_topic(self):
        processor = StatelessProcessor("inventory", {}, ("shipment.events",))
        runner = KafkaConsumerRunner(processor, bootstrap_servers="x")
        assert runner.topics == ("shipment.events", "retry.inventory")
        assert Topics.dead_letters("inventory") == "dlq.inventory"


class TestDeadLetterListing:
    def headers(self, event_id: str, **extra: str) -> dict[str, str]:
        return {"dlq.consumer": "inventory", "dlq.event-id": event_id, "dlq.status": "dead",
                "dlq.reason": "handler_failed", "dlq.attempts": "5", **extra}  # fmt: skip

    def test_a_replay_marker_flips_the_letter_to_replayed(self):
        records = [
            (self.headers("a"), b"{}", "k1"),
            (self.headers("b"), b"{}", "k2"),
            (
                self.headers("a", **{"dlq.status": "replayed", "dlq.replayed-at": "t"}),
                b"{}",
                "k1",
            ),
        ]
        letters = {x.event_id: x for x in collapse(records)}
        assert letters["a"].status == "replayed" and letters["a"].replays == ["t"]
        assert letters["b"].status == "dead" and letters["b"].attempts == 5
