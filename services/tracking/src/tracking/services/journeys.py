"""Journeys and field updates on MongoDB.

Every write is a single-document operation, so it is atomic without
transactions: inserting an update (deduplicated by its ``_id``) and updating
the journey with a *conditional* update that only moves forward in time
(``milestone_at < occurred_at``). Updates arriving late or out of order therefore
land on the timeline correctly without regressing the journey's current state.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from sl_platform.auth import Principal
from sl_platform.errors import ConflictError, DomainError, NotFoundError, PermissionDeniedError
from sl_platform.roles import UserRole
from tracking.milestones import (
    COURIER_MILESTONES,
    TERMINAL,
    JourneyStatus,
    Milestone,
    UpdateType,
)
from tracking.schemas import JourneyCreate, TrackingUpdate, UpdateResult
from tracking.store import TrackingStore

STAFF = frozenset(
    {UserRole.ADMIN, UserRole.CUSTOMER_SUPPORT, UserRole.WAREHOUSE_OPERATOR, UserRole.SYSTEM}
)


class JourneyClosedError(ConflictError):
    code = "journey_closed"


class InvalidUpdateError(DomainError):
    status_code = 422
    code = "invalid_update"


def now() -> datetime:
    return datetime.now(UTC)


class JourneyService:
    def __init__(self, store: TrackingStore, *, max_clock_skew_seconds: int = 300) -> None:
        self.store = store
        self.max_skew = timedelta(seconds=max_clock_skew_seconds)

    # --- journeys ----------------------------------------------------------------

    async def open(self, data: JourneyCreate) -> tuple[dict, bool]:
        """Create the journey at ``dispatched``, or return the existing one."""
        sid = str(data.shipment_id)
        existing = await self.store.journeys.find_one({"_id": sid})
        if existing is not None:
            if existing["status"] == JourneyStatus.CANCELLED:
                # A retried dispatch reopens it for the (possibly new) courier.
                existing = await self.store.journeys.find_one_and_update(
                    {"_id": sid},
                    {
                        "$set": {
                            "status": JourneyStatus.ACTIVE.value,
                            "courier_id": str(data.courier_id),
                            "milestone": Milestone.DISPATCHED.value,
                            "milestone_at": now(),
                            "updated_at": now(),
                            "cancel_reason": None,
                        }
                    },
                    return_document=ReturnDocument.AFTER,
                )
            return existing, False
        at = now()
        journey = {
            "_id": sid,
            "reference_no": data.reference_no,
            "courier_id": str(data.courier_id),
            "origin_warehouse_id": str(data.origin_warehouse_id),
            "destination_city": data.destination_city,
            "status": JourneyStatus.ACTIVE.value,
            "milestone": Milestone.DISPATCHED.value,
            "milestone_at": at,
            "delivery_attempts": 0,
            "last_location": None,
            "started_at": at,
            "updated_at": at,
            "completed_at": None,
            "cancel_reason": None,
        }
        try:
            await self.store.journeys.insert_one(journey)
        except DuplicateKeyError:  # a concurrent open won
            return await self.store.journeys.find_one({"_id": sid}), False
        await self._record_system_event(
            event_id=uuid.uuid5(uuid.NAMESPACE_URL, f"journey-opened:{sid}:{at.isoformat()}"),
            shipment_id=sid,
            milestone=Milestone.DISPATCHED,
            occurred_at=at,
            courier_id=str(data.courier_id),
            source="dispatch",
        )
        return journey, True

    async def cancel(self, shipment_id: uuid.UUID, reason: str | None) -> dict:
        journey = await self.store.journeys.find_one_and_update(
            {"_id": str(shipment_id), "status": {"$ne": JourneyStatus.COMPLETED.value}},
            {
                "$set": {
                    "status": JourneyStatus.CANCELLED.value,
                    "cancel_reason": reason,
                    "updated_at": now(),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if journey is None:
            existing = await self.store.journeys.find_one({"_id": str(shipment_id)})
            if existing is None:
                raise NotFoundError("No journey for this shipment.")
            raise JourneyClosedError("A completed journey cannot be cancelled.")
        return journey

    async def get(self, shipment_id: uuid.UUID, principal: Principal) -> dict:
        journey = await self.store.journeys.find_one({"_id": str(shipment_id)})
        if journey is None or not self._may_see(principal, journey):
            raise NotFoundError("No journey for this shipment.")
        return journey

    async def events(self, shipment_id: uuid.UUID, *, limit: int = 200) -> list[dict]:
        cursor = (
            self.store.events.find({"shipment_id": str(shipment_id)})
            .sort([("occurred_at", 1), ("received_at", 1)])
            .limit(limit)
        )
        return [event async for event in cursor]

    async def by_reference(self, reference_no: str) -> dict:
        journey = await self.store.journeys.find_one({"reference_no": reference_no})
        if journey is None:
            raise NotFoundError("Unknown tracking number.")
        return journey

    async def reassign(self, shipment_id: str, courier_id: str) -> None:
        """Follow ``courier.assigned``: a reassigned shipment's updates come from
        the new courier from now on."""
        await self.store.journeys.update_one(
            {"_id": shipment_id, "status": JourneyStatus.ACTIVE.value},
            {"$set": {"courier_id": courier_id, "updated_at": now()}},
        )

    # --- field updates -------------------------------------------------------------

    async def record(self, update: TrackingUpdate, principal: Principal) -> UpdateResult:
        """Record one update. Duplicates and rejections are results, not errors,
        so a batch from a device that was offline is processed item by item."""
        try:
            status = await self._record(update, principal)
        except DomainError as error:
            return UpdateResult(
                event_id=update.event_id, status="rejected", code=error.code, message=str(error)
            )
        return UpdateResult(event_id=update.event_id, status=status)

    async def _record(self, update: TrackingUpdate, principal: Principal) -> str:
        sid = str(update.shipment_id)
        if await self.store.events.find_one({"_id": str(update.event_id)}, {"_id": 1}):
            return "duplicate"
        journey = await self.store.journeys.find_one({"_id": sid})
        if journey is None:
            raise NotFoundError("No journey for this shipment.")
        if principal.role is UserRole.COURIER and journey["courier_id"] != str(
            principal.courier_id
        ):
            raise PermissionDeniedError("This shipment is not assigned to you.")
        if journey["status"] == JourneyStatus.CANCELLED:
            raise JourneyClosedError("This journey was cancelled.")
        if update.occurred_at > now() + self.max_skew:
            raise InvalidUpdateError("occurred_at is in the future; check the device clock.")
        if update.type is UpdateType.MILESTONE:
            if update.milestone not in COURIER_MILESTONES:
                raise InvalidUpdateError(f"{update.milestone} is not reported from the field.")
            if journey["status"] == JourneyStatus.COMPLETED:
                raise JourneyClosedError("This journey is already complete.")

        document: dict[str, Any] = {
            "_id": str(update.event_id),
            "shipment_id": sid,
            "type": update.type.value,
            "milestone": update.milestone.value if update.milestone else None,
            "occurred_at": update.occurred_at,
            "received_at": now(),
            "courier_id": str(principal.courier_id) if principal.courier_id else None,
            "location": update.location.model_dump() if update.location else None,
            "failure_reason": update.failure_reason,
            "note": update.note,
            "source": "courier",
            # The event is its own outbox entry; the worker's relay publishes it.
            "published": False,
        }
        try:
            await self.store.events.insert_one(document)
        except DuplicateKeyError:
            return "duplicate"
        await self._apply(sid, document)
        return "recorded"

    async def _apply(self, sid: str, event: dict) -> None:
        occurred = event["occurred_at"]
        if event["location"]:
            await self.store.journeys.update_one(
                {
                    "_id": sid,
                    "$or": [
                        {"last_location": None},
                        {"last_location.at": {"$lt": occurred}},
                    ],
                },
                {"$set": {"last_location": {**event["location"], "at": occurred}}},
            )
        milestone = event["milestone"]
        if not milestone:
            return
        changes: dict[str, Any] = {
            "milestone": milestone,
            "milestone_at": occurred,
            "updated_at": now(),
        }
        if milestone in TERMINAL:
            changes["status"] = JourneyStatus.COMPLETED.value
            changes["completed_at"] = occurred
        # Only a newer milestone moves the journey; a late one just joins the
        # timeline. The first field milestone always moves it off `dispatched`,
        # whose time is the server's: a device clock a little behind must not
        # leave the journey stuck there.
        await self.store.journeys.update_one(
            {
                "_id": sid,
                "$or": [
                    {"milestone_at": {"$lt": occurred}},
                    {"milestone": Milestone.DISPATCHED.value},
                ],
            },
            {"$set": changes},
        )
        if milestone == Milestone.DELIVERY_FAILED:
            # Counted per recorded event: the event's _id already deduplicated it.
            await self.store.journeys.update_one({"_id": sid}, {"$inc": {"delivery_attempts": 1}})

    async def record_from_shipment(
        self, *, event_id: uuid.UUID, shipment_id: str, milestone: Milestone, occurred_at: datetime
    ) -> None:
        """Milestones decided outside the field (a return started by support,
        received at the warehouse) still belong on the journey's timeline."""
        if await self.store.journeys.find_one({"_id": shipment_id}, {"_id": 1}) is None:
            return
        inserted = await self._record_system_event(
            event_id=event_id,
            shipment_id=shipment_id,
            milestone=milestone,
            occurred_at=occurred_at,
            courier_id=None,
            source="shipment",
        )
        if inserted:
            await self._apply(
                shipment_id,
                {"location": None, "milestone": milestone.value, "occurred_at": occurred_at},
            )

    async def _record_system_event(
        self,
        *,
        event_id: uuid.UUID,
        shipment_id: str,
        milestone: Milestone,
        occurred_at: datetime,
        courier_id: str | None,
        source: str,
    ) -> bool:
        try:
            await self.store.events.insert_one(
                {
                    "_id": str(event_id),
                    "shipment_id": shipment_id,
                    "type": UpdateType.MILESTONE.value,
                    "milestone": milestone.value,
                    "occurred_at": occurred_at,
                    "received_at": now(),
                    "courier_id": courier_id,
                    "location": None,
                    "failure_reason": None,
                    "note": None,
                    "source": source,
                    "published": False,
                }
            )
        except DuplicateKeyError:
            return False
        return True

    @staticmethod
    def _may_see(principal: Principal, journey: dict) -> bool:
        if principal.role in STAFF:
            if principal.role is UserRole.WAREHOUSE_OPERATOR:
                return principal.may_touch_warehouse(uuid.UUID(journey["origin_warehouse_id"]))
            return True
        return principal.courier_id is not None and journey["courier_id"] == str(
            principal.courier_id
        )
