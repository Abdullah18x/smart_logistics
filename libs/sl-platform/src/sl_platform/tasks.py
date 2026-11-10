"""Celery background jobs, the same way in every service.

Kafka carries facts other services react to; Celery runs a service's *own*
deferred or scheduled work — send this email (and retry it), sweep expired
holds every five minutes, recompute yesterday's metrics at night. Each
service has its own queue on RabbitMQ, so one service's backlog never delays
another's jobs.

Reliability settings, and why:

* ``acks_late`` + ``reject_on_worker_lost`` — a task is acknowledged only when
  it finishes, so a worker killed mid-task hands it to another worker instead
  of losing it. Tasks must therefore be idempotent (they all are: keyed by a
  delivery id, or conditional updates).
* ``prefetch_multiplier = 1`` — a worker holds one task at a time, so a slow
  task never sits on a queue of others.
* no result backend — callers never wait for results; outcomes are recorded
  in the service's database and in metrics.

Our code is asyncio and Celery's is not: a worker runs its tasks one at a time
(``--pool=solo``; scale with more workers) on one event loop per process,
through ``run_async``. The database engine binds to that loop and stays valid.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Coroutine
from typing import Any

from celery import Celery, signals

from sl_platform.config import ServiceSettings
from sl_platform.logging_setup import configure_logging
from sl_platform.metrics import TASK_SECONDS, TASKS, serve_metrics
from sl_platform.telemetry import instrument_worker

logger = logging.getLogger("sl_platform.tasks")

_loop: asyncio.AbstractEventLoop | None = None
_started: dict[str, float] = {}


def run_async[T](coroutine: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine on this process's event loop (created on first use)."""
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
    return _loop.run_until_complete(coroutine)


def create_celery(
    settings: ServiceSettings, *, beat_schedule: dict[str, dict] | None = None, engine: Any = None
) -> Celery:
    """The service's Celery app: its own queue, safe acknowledgement, metrics
    on ``METRICS_PORT``, logs in the service's format, traces continued from
    whoever queued the task."""
    name = settings.service_name
    app = Celery(name, broker=settings.celery_broker_url)
    app.conf.update(
        task_default_queue=name,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_ignore_result=True,
        result_backend=None,
        task_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        task_time_limit=300,
        task_soft_time_limit=240,
        broker_connection_retry_on_startup=True,
        worker_hijack_root_logger=False,
        beat_schedule=beat_schedule or {},
    )

    @signals.setup_logging.connect(weak=False)
    def _logging(**_: Any) -> None:
        configure_logging(f"{name}-celery", settings.log_level, settings.log_json)

    @signals.worker_process_init.connect(weak=False)
    @signals.worker_init.connect(weak=False)
    def _observability(**_: Any) -> None:
        instrument_worker(f"{name}-celery", settings.otel_exporter_otlp_endpoint, engine)
        _instrument_celery()
        serve_metrics(settings.metrics_port)

    return app


def _instrument_celery() -> None:
    """Carry trace context in task headers: a task queued while handling a
    request or an event runs as part of that trace."""
    try:
        from opentelemetry.instrumentation.celery import CeleryInstrumentor
    except ImportError:
        return
    if not CeleryInstrumentor().is_instrumented_by_opentelemetry:
        CeleryInstrumentor().instrument()


@signals.task_prerun.connect(weak=False)
def _task_started(task_id: str | None = None, **_: Any) -> None:
    if task_id:
        _started[task_id] = time.perf_counter()


@signals.task_postrun.connect(weak=False)
def _task_finished(
    task_id: str | None = None, task: Any = None, state: str | None = None, **_: Any
) -> None:
    started = _started.pop(task_id, None) if task_id else None
    name = getattr(task, "name", "unknown")
    if started is not None:
        TASK_SECONDS.labels(name).observe(time.perf_counter() - started)
    outcome = {"SUCCESS": "succeeded", "RETRY": "retried"}.get(state or "", "failed")
    TASKS.labels(name, outcome).inc()
