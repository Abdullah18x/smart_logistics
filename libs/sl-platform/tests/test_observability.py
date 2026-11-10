"""Metrics, tracing, log correlation and the Celery factory. No broker or database."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import APIRouter
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import REGISTRY

from sl_platform.app import create_service_app
from sl_platform.config import ServiceSettings
from sl_platform.consumer import StatelessProcessor, handle_with_retries
from sl_platform.events import EventEnvelope
from sl_platform.logging_setup import JsonFormatter, TextFormatter
from sl_platform.outbox import publish_envelope
from sl_platform.tasks import create_celery, run_async
from sl_platform.telemetry import consume_span, current_traceparent

EXPORTER = InMemorySpanExporter()


@pytest.fixture(scope="module", autouse=True)
def tracer_provider():
    """One in-memory tracer provider for this module (OpenTelemetry allows one
    global provider per process)."""
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(EXPORTER))
    trace.set_tracer_provider(provider)
    yield provider


@pytest.fixture(autouse=True)
def clear_spans():
    EXPORTER.clear()


def sample(name: str, labels: dict[str, str]) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def make_app():
    router = APIRouter()

    @router.get("/api/v1/things/{thing_id}")
    async def get_thing(thing_id: str) -> dict:
        return {"id": thing_id}

    settings = ServiceSettings(service_name="probe", log_json=False)
    return create_service_app(
        settings=settings, database=None, title="probe", description="", routers=[router]
    )


class TestHttpMetrics:
    async def test_requests_are_counted_by_route_template_not_raw_path(self):
        app = make_app()
        labels = {"method": "GET", "route": "/api/v1/things/{thing_id}", "status": "200"}
        before = sample("http_requests_total", labels)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:
            await c.get("/api/v1/things/a")
            await c.get("/api/v1/things/b")
            body = (await c.get("/metrics")).text
        assert sample("http_requests_total", labels) == before + 2
        assert "/api/v1/things/a" not in body  # one series per endpoint, not per id

    async def test_unknown_paths_share_one_label_and_probes_are_not_counted(self):
        app = make_app()
        unmatched = {"method": "GET", "route": "unmatched", "status": "404"}
        health = {"method": "GET", "route": "/health", "status": "200"}
        before = sample("http_requests_total", unmatched)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:
            await c.get("/nope/1")
            await c.get("/nope/2")
            await c.get("/health")
        assert sample("http_requests_total", unmatched) == before + 2
        assert sample("http_requests_total", health) == 0

    async def test_a_failing_refresher_never_breaks_the_scrape(self):
        from sl_platform.metrics import metrics_router

        async def broken() -> None:
            raise ConnectionError("database down")

        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(metrics_router([broken]))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:
            response = await c.get("/metrics")
        assert response.status_code == 200
        assert "http_requests_total" in response.text


class RecordingPublisher:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, dict, dict]] = []

    async def start(self) -> None: ...
    async def stop(self) -> None: ...

    async def send(self, topic, key, value, headers=None) -> None:
        self.sent.append((topic, key, json.loads(value), headers or {}))


class TestTraceThroughKafka:
    async def test_one_trace_runs_from_the_request_through_publish_to_the_consumer(self):
        tracer = trace.get_tracer("test")
        with tracer.start_as_current_span("POST /api/v1/shipments") as request_span:
            staged = EventEnvelope(
                event_type="shipment.created",
                producer="shipment",
                key="s-1",
                payload={},
                traceparent=current_traceparent(),
            ).model_dump(mode="json")
        # Later, in the worker process: the relay publishes it.
        publisher = RecordingPublisher()
        await publish_envelope(publisher, "shipment.events", staged)
        _, _, sent, headers = publisher.sent[0]
        # Then a consumer handles it.
        with consume_span(
            event_type=sent["event_type"],
            event_id=sent["event_id"],
            traceparent=sent["traceparent"],
            consumer="inventory",
        ):
            pass

        spans = {s.name: s for s in EXPORTER.get_finished_spans()}
        publish, process = spans["shipment.events publish"], spans["shipment.created process"]
        trace_id = request_span.get_span_context().trace_id
        assert {publish.context.trace_id, process.context.trace_id} == {trace_id}
        assert publish.parent.span_id == request_span.get_span_context().span_id
        assert process.parent.span_id == publish.context.span_id
        # The trace context also travels as a Kafka header, for header-only tools.
        assert headers["traceparent"] == sent["traceparent"]
        assert sent["traceparent"] != staged["traceparent"]  # re-stamped by the publish span

    async def test_a_handler_error_is_recorded_on_the_span(self):
        with (
            pytest.raises(ValueError),
            consume_span(event_type="x.happened", event_id="1", traceparent=None, consumer="c"),
        ):
            raise ValueError("boom")
        (span,) = EXPORTER.get_finished_spans()
        assert span.status.status_code.name == "ERROR"


def envelope(event_type="thing.happened", *, age_seconds=0.0) -> EventEnvelope:
    return EventEnvelope(
        event_type=event_type,
        producer="test",
        key="k",
        payload={},
        occurred_at=datetime.now(UTC) - timedelta(seconds=age_seconds),
    )


class TestConsumerOutcomes:
    async def test_processed_events_are_counted_and_their_end_to_end_latency_observed(self):
        consumer = f"c-{uuid.uuid4().hex[:6]}"

        async def ok(_):
            return None

        processor = StatelessProcessor(consumer, {"thing.happened": ok}, ("t",))
        outcome = await handle_with_retries(processor, envelope(age_seconds=2))
        assert outcome == "processed"
        labels = {"consumer": consumer, "event_type": "thing.happened"}
        assert sample("events_consumed_total", {**labels, "outcome": "processed"}) == 1
        assert sample("event_end_to_end_seconds_count", labels) == 1
        assert sample("event_end_to_end_seconds_sum", labels) >= 2

    async def test_unknown_event_types_are_ignored_not_failed(self):
        consumer = f"c-{uuid.uuid4().hex[:6]}"
        processor = StatelessProcessor(consumer, {}, ("t",))
        assert await handle_with_retries(processor, envelope()) == "ignored"
        labels = {"consumer": consumer, "event_type": "thing.happened", "outcome": "ignored"}
        assert sample("events_consumed_total", labels) == 1

    async def test_a_poison_event_is_retried_then_given_up_on(self):
        consumer = f"c-{uuid.uuid4().hex[:6]}"
        calls = []

        async def broken(_):
            calls.append(1)
            raise RuntimeError("always")

        processor = StatelessProcessor(consumer, {"thing.happened": broken}, ("t",))
        outcome = await handle_with_retries(
            processor, envelope(), max_attempts=3, backoff=lambda _: 0
        )
        assert outcome == "gave_up" and len(calls) == 3
        labels = {"consumer": consumer, "event_type": "thing.happened"}
        assert sample("events_consumed_total", {**labels, "outcome": "failed"}) == 3
        assert sample("events_consumed_total", {**labels, "outcome": "gave_up"}) == 1


class TestLogCorrelation:
    def record(self) -> logging.LogRecord:
        return logging.LogRecord("x", logging.INFO, __file__, 1, "hello", None, None)

    def test_json_lines_carry_the_trace_id_inside_a_span(self):
        with trace.get_tracer("t").start_as_current_span("work") as span:
            line = json.loads(JsonFormatter("svc").format(self.record()))
        assert line["trace_id"] == format(span.get_span_context().trace_id, "032x")
        assert len(line["span_id"]) == 16

    def test_text_lines_append_the_trace_id_and_omit_it_outside_a_span(self):
        with trace.get_tracer("t").start_as_current_span("work") as span:
            inside = TextFormatter("svc").format(self.record())
        outside = TextFormatter("svc").format(self.record())
        assert inside.endswith(f"trace={format(span.get_span_context().trace_id, '032x')}")
        assert "trace=" not in outside


class TestCelery:
    def test_each_service_gets_its_own_queue_and_safe_acknowledgement(self):
        app = create_celery(ServiceSettings(service_name="inventory"))
        assert app.conf.task_default_queue == "inventory"
        assert app.conf.task_acks_late and app.conf.task_reject_on_worker_lost
        assert app.conf.worker_prefetch_multiplier == 1
        assert app.conf.task_ignore_result

    def test_beat_schedules_are_passed_through(self):
        schedule = {"sweep": {"task": "inventory.sweep", "schedule": 300}}
        app = create_celery(ServiceSettings(service_name="inventory"), beat_schedule=schedule)
        assert app.conf.beat_schedule == schedule

    def test_run_async_reuses_one_loop_per_process(self):
        async def which_loop():
            return asyncio.get_running_loop()

        assert run_async(which_loop()) is run_async(which_loop())

    def test_task_outcomes_are_counted(self):
        app = create_celery(ServiceSettings(service_name="probe"))
        app.conf.task_always_eager = True
        name = f"probe.job_{uuid.uuid4().hex[:6]}"

        @app.task(name=name)
        def job(fail: bool) -> None:
            if fail:
                raise RuntimeError("nope")

        job.delay(False)
        with pytest.raises(RuntimeError):
            job.apply(args=(True,), throw=True)
        assert sample("celery_tasks_total", {"task": name, "outcome": "succeeded"}) == 1
        assert sample("celery_tasks_total", {"task": name, "outcome": "failed"}) == 1
        assert sample("celery_task_duration_seconds_count", {"task": name}) == 2


def test_scrapes_and_probes_are_kept_out_of_the_access_log():
    import logging

    from sl_platform.logging_setup import QuietPathsFilter

    quiet = QuietPathsFilter()

    def record(path: str, status: int) -> logging.LogRecord:
        return logging.LogRecord(
            "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
            ("127.0.0.1:5000", "GET", path, "1.1", status), None,
        )  # fmt: skip

    assert not quiet.filter(record("/metrics", 200))
    assert not quiet.filter(record("/health/ready", 200))
    assert quiet.filter(record("/health/ready", 503))  # a failing probe is still shown
    assert quiet.filter(record("/api/v1/shipments", 200))
    assert quiet.filter(record("/metrics-review", 200))
