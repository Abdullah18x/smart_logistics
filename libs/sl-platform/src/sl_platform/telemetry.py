"""OpenTelemetry wiring. A no-op unless the ``otel`` extra is installed and an
OTLP endpoint is configured, so tests and minimal setups need neither."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("sl_platform.telemetry")


def current_traceparent() -> str | None:
    """The W3C ``traceparent`` for the active span, if tracing is on."""
    try:
        from opentelemetry import propagate
    except ImportError:
        return None
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return carrier.get("traceparent")


def setup_telemetry(app: Any, *, service_name: str, endpoint: str | None, engine: Any) -> None:
    if not endpoint:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning("OTEL endpoint set but the 'otel' extra is not installed; tracing off")
        return

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces"))
    )
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, excluded_urls="/health,/health/ready")
    HTTPXClientInstrumentor().instrument()
    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)
