"""Tracing and metrics wiring of the orchestrator. No Temporal server needed."""

from __future__ import annotations

import asyncio
import uuid

from prometheus_client import REGISTRY
from temporalio.contrib.opentelemetry import TracingInterceptor

from orchestrator import trigger
from orchestrator.config import Settings
from orchestrator.temporal import metrics_runtime, tracing_interceptors
from sl_platform.events import EventEnvelope


def counted(event_type: str, outcome: str) -> float:
    labels = {"consumer": "orchestrator", "event_type": event_type, "outcome": outcome}
    return REGISTRY.get_sample_value("events_consumed_total", labels) or 0.0


def event(event_type: str) -> EventEnvelope:
    return EventEnvelope(event_type=event_type, producer="test", key="k", payload={})


def test_workflows_and_activities_join_the_callers_trace():
    assert any(isinstance(i, TracingInterceptor) for i in tracing_interceptors())


def test_only_the_worker_exports_sdk_metrics():
    assert metrics_runtime(None) is None


async def test_delivered_events_are_counted_by_outcome(monkeypatch):
    event_type = f"x.{uuid.uuid4().hex[:6]}"

    async def acted(*_):
        return True

    monkeypatch.setattr(trigger, "handle", acted)
    await trigger.deliver(object(), Settings(), event(event_type), asyncio.Event())
    assert counted(event_type, "processed") == 1


async def test_a_dispatch_request_is_retried_until_temporal_accepts_it(monkeypatch):
    event_type = f"x.{uuid.uuid4().hex[:6]}"
    attempts = []

    async def flaky(*_):
        attempts.append(1)
        if len(attempts) < 3:
            raise ConnectionError("temporal is restarting")
        return True

    async def no_wait(_):
        return None

    monkeypatch.setattr(trigger, "handle", flaky)
    monkeypatch.setattr(trigger.asyncio, "sleep", no_wait)
    await trigger.deliver(object(), Settings(), event(event_type), asyncio.Event())
    assert len(attempts) == 3
    assert counted(event_type, "failed") == 2 and counted(event_type, "processed") == 1


class RecordingPublisher:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    async def send(self, topic, key, value, headers=None):
        self.sent.append((topic, headers or {}))


async def test_an_unreadable_message_is_dead_lettered_not_dropped():
    from orchestrator.trigger import triage
    from sl_platform.dlq import DeadLetters

    publisher = RecordingPublisher()
    envelope = await triage(
        b"garbage", topic="shipment.events", key="k", registry=None,
        dead_letters=DeadLetters(publisher),
    )  # fmt: skip
    assert envelope is None
    topic, headers = publisher.sent[0]
    assert topic == "dlq.orchestrator" and headers["dlq.reason"] == "unparseable"


async def test_events_it_acts_on_pass_through_triage():
    from orchestrator.trigger import DISPATCH_REQUESTED, triage

    raw = event(DISPATCH_REQUESTED).to_bytes()
    envelope = await triage(raw, topic="shipment.events", key="k", registry=None, dead_letters=None)
    assert envelope is not None and envelope.event_type == DISPATCH_REQUESTED
