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
from sl_platform.events import EventEnvelope, Topics
from sl_platform.worker import retry_until_ready

logger = logging.getLogger("orchestrator.trigger")
DISPATCH_REQUESTED = "shipment.dispatch_requested"
MILESTONE_RECORDED = "tracking.milestone_recorded"


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


async def run_trigger(client: Client, settings: Settings, stop: asyncio.Event) -> None:
    from aiokafka import AIOKafkaConsumer

    consumer: AIOKafkaConsumer | None = None

    async def connect() -> None:
        nonlocal consumer
        candidate = AIOKafkaConsumer(
            Topics.SHIPMENT,
            Topics.TRACKING,
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
    try:
        while not stop.is_set():
            batch = await consumer.getmany(timeout_ms=500)
            for _, messages in batch.items():
                for message in messages:
                    try:
                        envelope = EventEnvelope.from_bytes(message.value)
                    except Exception:
                        logger.exception("dropping an unparseable message")
                        continue
                    while not stop.is_set():
                        try:
                            await handle(client, settings, envelope)
                            break
                        except Exception:
                            logger.exception("could not start dispatch; retrying in 5s")
                            await asyncio.sleep(5)
            if batch:
                await consumer.commit()
    finally:
        await consumer.stop()
