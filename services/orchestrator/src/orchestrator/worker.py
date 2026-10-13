"""Orchestrator background process: ``python -m orchestrator.worker``.

Runs the Temporal worker (dispatch workflow + activities) and the trigger that
starts workflows from ``shipment.dispatch_requested`` events. Scale it like any
worker: Temporal spreads workflow tasks over every running copy.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from temporalio.worker import Worker

from orchestrator.activities import DispatchActivities
from orchestrator.clients import build_clients
from orchestrator.config import settings
from orchestrator.temporal import connect
from orchestrator.trigger import run_trigger
from orchestrator.workflows.delivery import DeliveryWorkflow
from orchestrator.workflows.dispatch import DispatchWorkflow
from sl_platform.logging_setup import configure_logging

logger = logging.getLogger("orchestrator.worker")


async def run() -> None:
    configure_logging("orchestrator-worker", settings.log_level, settings.log_json)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # Windows
            loop.add_signal_handler(sig, stop.set)

    client = await connect(settings, stop)
    if client is None:
        return
    clients = build_clients(settings)
    activities = DispatchActivities(
        shipment=clients.shipment,
        inventory=clients.inventory,
        courier=clients.courier,
        tracking=clients.tracking,
    )
    worker = Worker(
        client,
        task_queue=settings.task_queue,
        workflows=[DispatchWorkflow, DeliveryWorkflow],
        activities=activities.all(),
    )
    logger.info("orchestrator worker started on task queue %s", settings.task_queue)
    tasks = [
        asyncio.create_task(worker.run(), name="temporal-worker"),
        asyncio.create_task(run_trigger(client, settings, stop), name="dispatch-trigger"),
    ]
    try:
        await stop.wait()
    finally:
        await worker.shutdown()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await clients.aclose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
