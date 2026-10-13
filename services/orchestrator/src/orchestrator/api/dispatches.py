"""Read-only view of dispatch workflows, for operators and dashboards."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.service import RPCError

from orchestrator.config import settings
from sl_platform.auth import Auth, Principal, TokenVerifier
from sl_platform.errors import NotFoundError, UpstreamUnavailableError
from sl_platform.roles import UserRole
from sl_platform.schemas import Problem

verifier = TokenVerifier.from_settings(settings)
auth = Auth(verifier)
StaffPrincipal = Annotated[
    Principal,
    Depends(
        auth.require(
            UserRole.ADMIN,
            UserRole.CUSTOMER_SUPPORT,
            UserRole.WAREHOUSE_OPERATOR,
            UserRole.SYSTEM,
        )
    ),
]

router = APIRouter(prefix="/api/v1/dispatches", tags=["Dispatches"])


class TemporalHandle:
    """One lazily connected Temporal client per process."""

    client: Client | None = None

    async def get(self) -> Client:
        if self.client is None:
            try:
                self.client = await Client.connect(
                    settings.temporal_address, namespace=settings.temporal_namespace
                )
            except Exception as exc:
                raise UpstreamUnavailableError("Temporal is unavailable.") from exc
        return self.client

    async def check(self) -> None:
        await (await self.get()).service_client.check_health()


temporal = TemporalHandle()


class DispatchView(BaseModel):
    workflow_id: str
    run_id: str | None
    status: str
    started_at: str | None
    closed_at: str | None
    step: str | None = None
    done: list[str] = []
    result: dict | None = None


@router.get(
    "/{workflow_id}",
    response_model=DispatchView,
    summary="Progress of one dispatch workflow",
    responses={404: {"model": Problem}, 503: {"model": Problem}},
)
async def get_dispatch(workflow_id: str, _staff: StaffPrincipal) -> DispatchView:
    """`workflow_id` is the shipment's `dispatch_workflow_id`
    (`dispatch-<shipment>-<attempt>`). Shows the current step while running and
    the outcome once finished; the Temporal UI (:8233) has the full history."""
    client = await temporal.get()
    handle = client.get_workflow_handle(workflow_id)
    try:
        info = await handle.describe()
    except RPCError as exc:
        raise NotFoundError("No such dispatch.") from exc
    view = DispatchView(
        workflow_id=workflow_id,
        run_id=info.run_id,
        status=info.status.name.lower() if info.status else "unknown",
        started_at=info.start_time.isoformat() if info.start_time else None,
        closed_at=info.close_time.isoformat() if info.close_time else None,
    )
    if info.status is WorkflowExecutionStatus.RUNNING:
        progress = await handle.query("progress")
        view.step, view.done = progress.get("step"), progress.get("done", [])
    elif info.status is WorkflowExecutionStatus.COMPLETED:
        result = await handle.result()
        view.result = result if isinstance(result, dict) else result.__dict__
        view.done = view.result.get("steps", [])
    return view
