"""Dispatch activities against fake services: error mapping and idempotency."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from orchestrator.activities import DispatchActivities
from orchestrator.contracts import EndInput, MarkDispatchedInput, ShipmentFacts
from sl_platform.errors import RemoteError, UpstreamUnavailableError
from sl_platform.http import ServiceClient

WORKFLOW_ID = "dispatch-s1-1"


def shipment_body(**overrides) -> dict:
    body = {
        "id": "s1",
        "reference_no": "SL-2026-000001",
        "status": "dispatching",
        "dispatch_workflow_id": WORKFLOW_ID,
        "origin_warehouse_id": str(uuid.uuid4()),
        "packages": [{"weight_g": 1000}, {"weight_g": 500}],
        "items": [{"sku_id": "sku-1", "quantity": 2}],
        "destination_address": {"line1": "House 12", "city": "Karachi", "contact_phone": "+92"},
    }
    body.update(overrides)
    return body


def client(handler) -> ServiceClient:
    return ServiceClient("svc", "http://svc", retries=0, transport=httpx.MockTransport(handler))


def activities(**handlers) -> DispatchActivities:
    default = lambda r: httpx.Response(200, json={})  # noqa: E731
    return DispatchActivities(
        shipment=client(handlers.get("shipment", default)),
        inventory=client(handlers.get("inventory", default)),
        courier=client(handlers.get("courier", default)),
        tracking=client(handlers.get("tracking", default)),
    )


def env() -> ActivityEnvironment:
    environment = ActivityEnvironment()
    environment.info = environment.info.__class__(
        **{**environment.info.__dict__, "workflow_id": WORKFLOW_ID}
    )
    return environment


async def test_validate_returns_the_facts_the_workflow_needs():
    acts = activities(shipment=lambda r: httpx.Response(200, json=shipment_body()))
    facts = await env().run(acts.validate_shipment, "s1")
    assert facts.total_weight_g == 1500
    assert facts.package_count == 2
    assert facts.items == [("sku-1", 2)]


async def test_validate_refuses_an_incomplete_shipment_for_good():
    body = shipment_body(packages=[], destination_address={"city": "Karachi"})
    acts = activities(shipment=lambda r: httpx.Response(200, json=body))
    with pytest.raises(ApplicationError) as caught:
        await env().run(acts.validate_shipment, "s1")
    assert caught.value.type == "invalid_shipment" and caught.value.non_retryable
    assert "no packages" in caught.value.message


async def test_validate_spots_a_run_that_is_no_longer_current():
    body = shipment_body(dispatch_workflow_id="dispatch-s1-2")
    acts = activities(shipment=lambda r: httpx.Response(200, json=body))
    with pytest.raises(ApplicationError) as caught:
        await env().run(acts.validate_shipment, "s1")
    assert caught.value.type == "stale_dispatch"


async def test_confirm_stock_checks_every_line_is_held_in_full():
    def inventory(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"confirmed": 0})
        return httpx.Response(200, json=[{"sku_id": "sku-1", "quantity": 1, "status": "held"}])

    facts = ShipmentFacts("s1", "SL-1", "w", 1, 1, "Karachi", [("sku-1", 2)])
    with pytest.raises(ApplicationError) as caught:
        await env().run(activities(inventory=inventory).confirm_stock, facts)
    assert caught.value.type == "stock_not_held"


async def test_a_business_no_is_final_and_keeps_the_service_code():
    def courier(request):
        return httpx.Response(409, json={"code": "no_courier_available", "message": "none"})

    from orchestrator.contracts import AssignCourierInput

    with pytest.raises(ApplicationError) as caught:
        await env().run(
            activities(courier=courier).assign_courier, AssignCourierInput("s1", "w", 10)
        )
    assert caught.value.type == "no_courier_available" and caught.value.non_retryable


async def test_transient_failures_stay_retryable():
    acts = activities(shipment=lambda r: httpx.Response(503, json={}))
    with pytest.raises(UpstreamUnavailableError):
        await env().run(acts.generate_labels, "s1")
    busy = activities(
        shipment=lambda r: httpx.Response(409, json={"code": "concurrent_update", "message": "x"})
    )
    with pytest.raises(RemoteError):  # plain exception: Temporal retries it
        await env().run(busy.mark_dispatched, MarkDispatchedInput("s1", "c1"))


async def test_marking_is_idempotent_when_a_retry_finds_it_already_done():
    def shipment(request: httpx.Request) -> httpx.Response:
        if request.method == "PATCH":
            assert json.loads(request.content)["courier_id"] == "c1"
            return httpx.Response(409, json={"code": "conflict", "message": "already dispatched"})
        return httpx.Response(200, json=shipment_body(status="dispatched"))

    await env().run(activities(shipment=shipment).mark_dispatched, MarkDispatchedInput("s1", "c1"))


async def test_cancelling_a_journey_that_never_started_is_fine():
    acts = activities(
        tracking=lambda r: httpx.Response(404, json={"code": "not_found", "message": "none"})
    )
    await env().run(acts.cancel_tracking, EndInput("s1", "dispatch failed"))
