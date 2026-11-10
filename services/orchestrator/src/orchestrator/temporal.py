"""Connecting to Temporal, and starting dispatches."""

from __future__ import annotations

import asyncio
import logging

from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from orchestrator.config import Settings
from orchestrator.contracts import DispatchInput
from orchestrator.workflows.dispatch import DispatchWorkflow
from sl_platform.worker import retry_until_ready

logger = logging.getLogger("orchestrator.temporal")


def tracing_interceptors() -> list:
    """Temporal's OpenTelemetry interceptor, so a workflow, and every activity it
    runs, joins the trace of whatever started it (the Kafka event, an API call).
    Empty when OpenTelemetry is not installed."""
    try:
        from temporalio.contrib.opentelemetry import TracingInterceptor
    except ImportError:
        return []
    return [TracingInterceptor()]


def metrics_runtime(port: int | None):
    """A Temporal runtime that exports the SDK's own metrics (workflow and
    activity outcomes, latencies, task-queue polling) for Prometheus. Only the
    worker process sets a port; the API's client uses the default runtime."""
    if not port:
        return None
    from temporalio.runtime import PrometheusConfig, Runtime, TelemetryConfig

    try:
        return Runtime(telemetry=TelemetryConfig(metrics=PrometheusConfig(f"0.0.0.0:{port}")))
    except ValueError as exc:  # usually: the port is held by another copy of the worker
        logger.warning("Temporal SDK metrics on :%s not started: %s", port, exc)
        return None


async def connect(
    settings: Settings, stop: asyncio.Event | None = None, *, metrics_port: int | None = None
) -> Client | None:
    """Connect, waiting with backoff while Temporal starts. None if stopped first."""
    holder: list[Client] = []
    runtime = metrics_runtime(metrics_port)

    async def attempt() -> None:
        holder.append(
            await Client.connect(
                settings.temporal_address,
                namespace=settings.temporal_namespace,
                interceptors=tracing_interceptors(),
                **({"runtime": runtime} if runtime else {}),
            )
        )

    ok = await retry_until_ready(attempt, "temporal", stop or asyncio.Event())
    return holder[0] if ok else None


async def start_dispatch(
    client: Client, settings: Settings, *, workflow_id: str, request: DispatchInput
) -> bool:
    """Start one dispatch attempt. False if that attempt already ran or runs.

    The workflow id is ``dispatch-<shipment>-<attempt>`` and ids are never
    reused: a redelivered ``dispatch_requested`` event is a no-op even after
    the run finished, while a retry after a failure (a new attempt) starts.
    """
    try:
        await client.start_workflow(
            DispatchWorkflow.run,
            request,
            id=workflow_id,
            task_queue=settings.task_queue,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        )
    except WorkflowAlreadyStartedError:
        logger.info("dispatch %s already started; ignoring the duplicate request", workflow_id)
        return False
    logger.info("started %s", workflow_id)
    return True
