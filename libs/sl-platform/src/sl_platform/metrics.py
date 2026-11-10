"""Prometheus metrics shared by every service.

Each process exposes what it can see:

* the **API** serves ``/metrics`` on its own port: request rate, errors and
  latency per route (RED), plus gauges refreshed at scrape time — the outbox
  backlog lives here, so it is reported even while the worker that should
  drain it is down;
* the **Kafka worker** and the **Celery worker** have no HTTP server of their
  own, so they start one on ``METRICS_PORT``: events consumed, consumer lag,
  end-to-end event latency, dead letters, task outcomes.

No ``service`` label on any metric: Prometheus adds it from the scrape target,
so the same names work for every service and dashboards filter on it.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from fastapi import APIRouter, FastAPI, Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    start_http_server,
)
from sqlalchemy import func, select

logger = logging.getLogger("sl_platform.metrics")

LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.15, 0.25, 0.3, 0.5, 1, 2.5, 5, 10)
LAG_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 300)

# --- HTTP (RED per route) ----------------------------------------------------------
HTTP_REQUESTS = Counter(
    "http_requests_total", "HTTP requests handled.", ["method", "route", "status"]
)
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds",
    "Time to handle an HTTP request.",
    ["method", "route"],
    buckets=LATENCY_BUCKETS,
)
HTTP_IN_FLIGHT = Gauge("http_requests_in_flight", "HTTP requests being handled now.")

# --- Outbox ------------------------------------------------------------------------
OUTBOX_PENDING = Gauge("outbox_pending_events", "Events staged but not yet published.")
OUTBOX_OLDEST_AGE = Gauge(
    "outbox_oldest_pending_age_seconds",
    "Age of the oldest unpublished event; grows when the relay is stuck or down.",
)
OUTBOX_PUBLISHED = Counter("outbox_published_total", "Events published to Kafka.", ["topic"])
OUTBOX_PUBLISH_FAILURES = Counter(
    "outbox_publish_failures_total", "Failed publish attempts (retried).", ["topic"]
)

# --- Consumers ---------------------------------------------------------------------
EVENTS_CONSUMED = Counter(
    "events_consumed_total",
    "Events handled by a consumer, by outcome: processed, duplicate, ignored, "
    "failed (an attempt that will be retried), dead_lettered.",
    ["consumer", "event_type", "outcome"],
)
EVENT_HANDLE_SECONDS = Histogram(
    "event_handle_duration_seconds",
    "Time a handler took for one event.",
    ["consumer", "event_type"],
    buckets=LATENCY_BUCKETS,
)
EVENT_END_TO_END_SECONDS = Histogram(
    "event_end_to_end_seconds",
    "From the change being committed (event occurred_at) to a consumer's effect "
    "being committed. The 'event lag' target is p95 < 5 s.",
    ["consumer", "event_type"],
    buckets=LAG_BUCKETS,
)
DEAD_LETTERS = Counter(
    "dead_letters_total",
    "Events moved to a dead-letter topic, by reason: handler_failed, unparseable, "
    "contract_violation.",
    ["consumer", "reason"],
)
EVENTS_UNVALIDATED = Counter(
    "events_unvalidated_total",
    "Events handled without a registry check: no schema id (published before the "
    "registry, or by a test bus) or the registry was unreachable.",
    ["consumer", "reason"],
)
CONSUMER_LAG = Gauge(
    "kafka_consumer_lag_messages",
    "Messages a consumer group has not reached yet, per partition.",
    ["consumer", "topic", "partition"],
)

# --- Celery ------------------------------------------------------------------------
TASKS = Counter(
    "celery_tasks_total",
    "Celery task executions by outcome: succeeded, failed, retried.",
    ["task", "outcome"],
)
TASK_SECONDS = Histogram(
    "celery_task_duration_seconds", "Time a task took.", ["task"], buckets=LAG_BUCKETS
)

Refresher = Callable[[], Awaitable[None]]

_EXCLUDED = ("/metrics", "/health", "/health/ready")


def instrument_app(app: FastAPI) -> None:
    """Record RED metrics for every request, labelled by route *template*
    (``/api/v1/shipments/{shipment_id}``), never the raw path, so one label
    value per endpoint however many shipments there are."""

    @app.middleware("http")
    async def record(request: Request, call_next):
        if request.url.path in _EXCLUDED:
            return await call_next(request)
        HTTP_IN_FLIGHT.inc()
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            HTTP_IN_FLIGHT.dec()
            route = request.scope.get("route")
            template = getattr(route, "path", None) or "unmatched"
            HTTP_REQUESTS.labels(request.method, template, str(status)).inc()
            HTTP_LATENCY.labels(request.method, template).observe(time.perf_counter() - started)


def metrics_router(refreshers: Sequence[Refresher] = ()) -> APIRouter:
    """``GET /metrics``. Refreshers update gauges that are read from a store
    (the outbox backlog) just before the scrape."""
    router = APIRouter(include_in_schema=False)

    @router.get("/metrics")
    async def metrics() -> Response:
        for refresh in refreshers:
            try:
                await refresh()
            except Exception:  # a scrape must never fail because a store is down
                logger.warning("metrics refresher %s failed", getattr(refresh, "__name__", refresh))
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return router


def outbox_refresher(database: Any) -> Refresher:
    """Read the outbox backlog from the service's own database."""
    from sl_platform.db import utcnow
    from sl_platform.models import OutboxEvent

    async def refresh_outbox() -> None:
        async with database.sessionmaker() as session:
            pending, oldest = (
                await session.execute(
                    select(func.count(), func.min(OutboxEvent.created_at)).where(
                        OutboxEvent.published_at.is_(None)
                    )
                )
            ).one()
        OUTBOX_PENDING.set(pending)
        OUTBOX_OLDEST_AGE.set((utcnow() - oldest).total_seconds() if oldest else 0)

    return refresh_outbox


_serving: set[int] = set()


def serve_metrics(port: int | None) -> bool:
    """Start the Prometheus endpoint of a process without HTTP. Returns False
    when no port is configured (tests, one-off scripts). Calling it again for the
    same port is a no-op (Celery fires more than one start-up signal)."""
    if not port:
        return False
    if port in _serving:
        return True
    try:
        start_http_server(port)
    except OSError as exc:  # usually: another copy of this process holds the port
        logger.warning("metrics endpoint on :%s not started: %s", port, exc)
        return False
    _serving.add(port)
    logger.info("metrics on :%s/metrics", port)
    return True
