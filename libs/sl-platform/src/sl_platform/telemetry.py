"""OpenTelemetry wiring: one trace from an HTTP request, through the outbox and
Kafka, into the consumers, Celery tasks and Temporal activities it causes.

Everything here is a no-op unless the ``otel`` extra is installed, and spans are
only exported when an OTLP endpoint is configured — tests and minimal setups
need neither.

How a trace crosses the broker: the ``traceparent`` of the request is stamped
on the event when it is staged in the outbox (``EventEnvelope.traceparent``),
because the relay publishes it later, from another process. The relay opens a
*publish* span under that parent and re-stamps the envelope (and the Kafka
headers) with it; each consumer opens a *process* span under the publish span.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator
from typing import Any

logger = logging.getLogger("sl_platform.telemetry")

_provider_ready = False
_instrumented: set[str | int] = set()


def _instrument_clients(engine: Any) -> None:
    """httpx and SQLAlchemy, once per process (Celery fires two start-up signals)."""
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    if "httpx" not in _instrumented:
        HTTPXClientInstrumentor().instrument()
        _instrumented.add("httpx")
    if engine is not None and id(engine) not in _instrumented:
        # The instrumentation declares SQLAlchemy < 2.1 but only uses the engine
        # event API, which 2.1 keeps; without skip_dep_check it does nothing.
        SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine, skip_dep_check=True)
        _instrumented.add(id(engine))


try:
    from opentelemetry import propagate, trace
    from opentelemetry.trace import SpanKind, Status, StatusCode

    _tracer = trace.get_tracer("sl_platform")
except ImportError:  # the 'otel' extra is not installed
    propagate = trace = None  # type: ignore[assignment]
    _tracer = None


def setup_tracing(service_name: str, endpoint: str | None) -> bool:
    """Install the tracer provider for this process. Safe to call more than once.

    Every process that does work — the API, the Kafka worker, the Celery
    worker, the Temporal worker — calls it, so each shows up in Jaeger under
    its own service name.
    """
    global _provider_ready
    if _provider_ready or not endpoint or trace is None:
        return _provider_ready
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning("OTEL endpoint set but the 'otel' extra is not installed; tracing off")
        return False
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces"))
    )
    trace.set_tracer_provider(provider)
    _provider_ready = True
    return True


def setup_telemetry(app: Any, *, service_name: str, endpoint: str | None, engine: Any) -> None:
    """Tracing for an HTTP service: incoming requests, outgoing calls, SQL."""
    if not setup_tracing(service_name, endpoint):
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app, excluded_urls="/health,/health/ready,/metrics")
    _instrument_clients(engine)


def instrument_worker(service_name: str, endpoint: str | None, engine: Any = None) -> None:
    """Tracing for a process without HTTP: outgoing calls and SQL."""
    if not setup_tracing(service_name, endpoint):
        return
    _instrument_clients(engine)


def current_traceparent() -> str | None:
    """The W3C ``traceparent`` for the active span, if tracing is on."""
    if propagate is None:
        return None
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return carrier.get("traceparent")


def current_trace_ids() -> tuple[str, str] | None:
    """(trace_id, span_id) of the active span as hex, for log lines."""
    if trace is None:
        return None
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None
    return format(context.trace_id, "032x"), format(context.span_id, "016x")


def _parent(traceparent: str | None) -> Any:
    if propagate is None or not traceparent:
        return None
    return propagate.extract({"traceparent": traceparent})


@contextlib.contextmanager
def publish_span(envelope: dict[str, Any], topic: str) -> Iterator[dict[str, Any]]:
    """A PRODUCER span for one outbox row, under the request that staged it.

    Yields the envelope to send, re-stamped with this span's context so the
    consumers' spans hang under the publish.
    """
    if _tracer is None:
        yield envelope
        return
    with _tracer.start_as_current_span(
        f"{topic} publish",
        context=_parent(envelope.get("traceparent")),
        kind=SpanKind.PRODUCER,
        attributes={
            "messaging.system": "kafka",
            "messaging.destination.name": topic,
            "messaging.message.id": str(envelope.get("event_id")),
            "event.type": str(envelope.get("event_type")),
        },
    ):
        yield {**envelope, "traceparent": current_traceparent() or envelope.get("traceparent")}


@contextlib.contextmanager
def consume_span(
    *, event_type: str, event_id: Any, traceparent: str | None, consumer: str
) -> Iterator[Any]:
    """A CONSUMER span for handling one event, under the span that published it."""
    if _tracer is None:
        yield None
        return
    with _tracer.start_as_current_span(
        f"{event_type} process",
        context=_parent(traceparent),
        kind=SpanKind.CONSUMER,
        attributes={
            "messaging.system": "kafka",
            "messaging.consumer.group.name": consumer,
            "messaging.message.id": str(event_id),
            "event.type": event_type,
        },
    ) as span:
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise
