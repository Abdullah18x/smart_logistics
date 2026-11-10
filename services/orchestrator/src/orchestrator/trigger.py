"""Starts dispatch workflows from ``shipment.dispatch_requested`` events.

The shipment service stages the request in its outbox in the same transaction
that moves the shipment to ``dispatching``, so a request is never lost even if
the orchestrator is down; it is picked up when the orchestrator comes back.
No dedupe table is needed: Temporal refuses a second workflow with the same id.
An event is acknowledged only after the workflow was started (or found to be
started), so a Temporal outage delays dispatch instead of dropping it.
"""

from __future__ import annotations

import asyncio
import logging

from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode

from orchestrator.config import Settings
from orchestrator.contracts import DispatchInput, FieldUpdate
from orchestrator.temporal import start_dispatch
from sl_platform.consumer import check_contract, record_lag
from sl_platform.dlq import DeadLetters
from sl_platform.events import EventEnvelope, Topics
from sl_platform.metrics import EVENTS_CONSUMED
from sl_platform.outbox import KafkaPublisher
from sl_platform.schema_registry import SchemaRegistry
from sl_platform.telemetry import consume_span
from sl_platform.worker import retry_until_ready

logger = logging.getLogger("orchestrator.trigger")
DISPATCH_REQUESTED = "shipment.dispatch_requested"
MILESTONE_RECORDED = "tracking.milestone_recorded"
#: The event types this consumer acts on; everything else on its topics is ignored.
HANDLED = frozenset({DISPATCH_REQUESTED, MILESTONE_RECORDED})


async def handle(client: Client, settings: Settings, envelope: EventEnvelope) -> bool:
    if envelope.event_type == MILESTONE_RECORDED:
        return await forward_milestone(client, envelope)
    if envelope.event_type != DISPATCH_REQUESTED:
        return False
    payload = envelope.payload
    request = DispatchInput(
        shipment_id=str(payload["id"]),
        attempt=int(payload["attempt"]),
        warehouse_id=str(payload["origin_warehouse_id"]),
        courier_wait_attempts=settings.courier_wait_attempts,
        courier_wait_seconds=float(settings.courier_wait_seconds),
        pickup_timeout_seconds=float(settings.pickup_timeout_seconds),
        max_delivery_attempts=settings.max_delivery_attempts,
    )
    return await start_dispatch(
        client, settings, workflow_id=str(payload["workflow_id"]), request=request
    )


async def forward_milestone(client: Client, envelope: EventEnvelope) -> bool:
    """Signal a courier's milestone to the shipment's delivery workflow.

    Only field reports are forwarded (milestones mirrored from the shipment
    service would loop). The workflow ignores an ``event_id`` it has seen, so a
    redelivered event is harmless. No workflow (a journey opened by hand, or
    one already finished) is logged and skipped.
    """
    payload = envelope.payload
    if payload.get("source") != "courier" or not payload.get("milestone"):
        return False
    handle = client.get_workflow_handle(f"delivery-{payload['shipment_id']}")
    try:
        await handle.signal(
            "field_update",
            FieldUpdate(
                event_id=str(envelope.event_id),
                milestone=payload["milestone"],
                occurred_at=payload["occurred_at"],
                failure_reason=payload.get("failure_reason"),
            ),
        )
    except RPCError as error:
        if error.status in (RPCStatusCode.NOT_FOUND, RPCStatusCode.FAILED_PRECONDITION):
            logger.info("no running delivery workflow for %s", payload["shipment_id"])
            return False
        raise
    return True


async def deliver(
    client: Client, settings: Settings, envelope: EventEnvelope, stop: asyncio.Event
) -> None:
    """Act on one event, inside a CONSUMER span so the workflow it starts (or
    signals) joins the trace of the request behind the event.

    Never given up on: a dispatch request waits here, its offset uncommitted,
    until Temporal accepts it.
    """
    event_type = envelope.event_type
    while not stop.is_set():
        try:
            with consume_span(
                event_type=event_type,
                event_id=envelope.event_id,
                traceparent=envelope.traceparent,
                consumer="orchestrator",
            ):
                acted = await handle(client, settings, envelope)
        except Exception:
            EVENTS_CONSUMED.labels("orchestrator", event_type, "failed").inc()
            logger.exception("could not start dispatch; retrying in 5s")
            await asyncio.sleep(5)
            continue
        EVENTS_CONSUMED.labels(
            "orchestrator", event_type, "processed" if acted else "ignored"
        ).inc()
        return


async def triage(
    raw: bytes,
    *,
    topic: str | None,
    key: str | None,
    registry: SchemaRegistry | None,
    dead_letters: DeadLetters | None,
) -> EventEnvelope | None:
    """The envelope to act on, or None when the message was dead-lettered:
    unreadable, or an event we act on whose payload breaks its contract."""
    try:
        envelope = EventEnvelope.from_bytes(raw)
    except Exception as exc:
        await _dead_letter(dead_letters, raw, "unparseable", f"{type(exc).__name__}: {exc}",
                           topic=topic, key=key)  # fmt: skip
        return None
    if envelope.event_type in HANDLED:
        problem = await check_contract("orchestrator", envelope, registry)
        if problem is not None:
            await _dead_letter(dead_letters, raw, "contract_violation", problem,
                               topic=topic, key=key, envelope=envelope)  # fmt: skip
            return None
    return envelope


async def _dead_letter(
    dead_letters: DeadLetters | None,
    raw: bytes,
    reason: str,
    error: str,
    *,
    topic: str | None,
    key: str | None,
    envelope: EventEnvelope | None = None,
) -> None:
    event_type = envelope.event_type if envelope else "unknown"
    EVENTS_CONSUMED.labels("orchestrator", event_type, "dead_lettered").inc()
    logger.error("orchestrator dead-lettered a message (%s): %s", reason, error[:300])
    if dead_letters is not None:
        await dead_letters.send(
            "orchestrator",
            raw,
            reason=reason,
            error=error,
            source_topic=topic,
            key=key,
            event_id=str(envelope.event_id) if envelope else None,
            event_type=envelope.event_type if envelope else None,
        )


async def run_trigger(
    client: Client,
    settings: Settings,
    stop: asyncio.Event,
    *,
    registry: SchemaRegistry | None = None,
) -> None:
    from aiokafka import AIOKafkaConsumer

    consumer: AIOKafkaConsumer | None = None
    publisher = KafkaPublisher(settings.kafka_bootstrap_servers)

    async def connect() -> None:
        nonlocal consumer
        candidate = AIOKafkaConsumer(
            Topics.SHIPMENT,
            Topics.TRACKING,
            Topics.retry("orchestrator"),
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id="orchestrator",
            enable_auto_commit=False,
            auto_offset_reset="earliest",
        )
        try:
            await candidate.start()
        except Exception:
            await candidate.stop()
            raise
        consumer = candidate

    if not await retry_until_ready(connect, "dispatch trigger", stop):
        return
    if not await retry_until_ready(publisher.start, "dead-letter publisher", stop):
        await consumer.stop()
        return
    dead_letters = DeadLetters(publisher)
    try:
        while not stop.is_set():
            batch = await consumer.getmany(timeout_ms=500)
            for _, messages in batch.items():
                for message in messages:
                    envelope = await triage(
                        message.value,
                        topic=message.topic,
                        key=(message.key or b"").decode() or None,
                        registry=registry,
                        dead_letters=dead_letters,
                    )
                    if envelope is not None:
                        await deliver(client, settings, envelope, stop)
            if batch:
                await consumer.commit()
            await record_lag(consumer, "orchestrator")
    finally:
        await consumer.stop()
        await publisher.stop()
