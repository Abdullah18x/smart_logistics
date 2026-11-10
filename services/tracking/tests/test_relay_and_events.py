import uuid
from datetime import UTC, datetime, timedelta

from sl_platform.events import EventEnvelope
from tests.conftest import as_courier, at, open_journey
from tests.test_tracking import milestone
from tracking.api.deps import journeys, store
from tracking.events import build_processor
from tracking.relay import TrackingRelay


class FakePublisher:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, EventEnvelope]] = []

    async def send(self, topic: str, key: str, value: bytes, headers=None) -> None:
        self.sent.append((topic, key, EventEnvelope.from_bytes(value)))


async def test_the_relay_publishes_each_recorded_update_once(client):
    courier = uuid.uuid4()
    sid = (await open_journey(client, courier_id=courier))["shipment_id"]
    update = milestone(sid, "picked_up", 5)
    await client.post("/api/v1/tracking/updates", json=update, headers=as_courier(courier))
    publisher = FakePublisher()
    relay = TrackingRelay(store, publisher)
    assert await relay.publish_batch() == 2  # journey opened + picked up
    assert await relay.publish_batch() == 0
    topic, key, envelope = publisher.sent[-1]
    assert topic == "tracking.events" and key == sid
    assert envelope.event_type == "tracking.milestone_recorded"
    # Downstream dedupes on the device's event id.
    assert str(envelope.event_id) == update["event_id"]
    assert envelope.payload["milestone"] == "picked_up"


async def test_a_claim_left_by_a_crashed_relay_is_taken_over(client):
    sid = (await open_journey(client))["shipment_id"]
    await store.events.update_many(
        {"shipment_id": sid},
        {"$set": {"claimed_until": datetime.now(UTC) - timedelta(seconds=1)}},
    )
    publisher = FakePublisher()
    assert await TrackingRelay(store, publisher).publish_batch() == 1


async def test_a_reassignment_moves_updates_to_the_new_courier(client):
    old, new = uuid.uuid4(), uuid.uuid4()
    sid = (await open_journey(client, courier_id=old))["shipment_id"]
    processor = build_processor(journeys)
    await processor.process(
        EventEnvelope(
            event_type="courier.assigned",
            producer="courier",
            key=sid,
            payload={"shipment_id": sid, "courier_id": str(new)},
        )
    )
    denied = await client.post(
        "/api/v1/tracking/updates",
        json=milestone(sid, "picked_up", 5),
        headers=as_courier(old),
    )
    assert denied.json()["code"] == "permission_denied"
    allowed = await client.post(
        "/api/v1/tracking/updates",
        json=milestone(sid, "picked_up", 6),
        headers=as_courier(new),
    )
    assert allowed.json()["status"] == "recorded"


async def test_returns_started_elsewhere_join_the_timeline_once(client):
    sid = (await open_journey(client))["shipment_id"]
    event = EventEnvelope(
        event_type="shipment.status_changed",
        producer="shipment",
        key=sid,
        payload={"id": sid, "status": "return_initiated"},
    )
    processor = build_processor(journeys)
    await processor.process(event)
    await processor.process(event)  # redelivered
    events = await journeys.events(uuid.UUID(sid))
    assert [e["milestone"] for e in events].count("return_initiated") == 1
    assert (await store.journeys.find_one({"_id": sid}))["milestone"] == "return_initiated"
    assert at(0)  # helper stays importable


def test_published_events_match_their_contract_and_carry_the_trace():
    import uuid as _uuid
    from datetime import UTC, datetime

    from sl_platform.contracts import ContractError
    from tracking.relay import envelope_for

    event = {
        "_id": str(_uuid.uuid4()),
        "type": "milestone",
        "shipment_id": str(_uuid.uuid4()),
        "milestone": "picked_up",
        "occurred_at": datetime.now(UTC),
        "courier_id": str(_uuid.uuid4()),
        "location": {"latitude": 24.86, "longitude": 67.0, "accuracy_m": None},
        "failure_reason": None,
        "source": "courier",
        "traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
    }
    envelope = envelope_for(event)
    assert envelope.traceparent == event["traceparent"]
    try:
        envelope_for({**event, "milestone": "teleported"})
    except ContractError as exc:
        assert "milestone" in str(exc)
    else:
        raise AssertionError("an unknown milestone must break the contract")
