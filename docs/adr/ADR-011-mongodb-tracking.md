# ADR-011: MongoDB for tracking journeys

**Status:** Accepted, implemented in Week 2 · **Date:** 2026-10-12 · **Carries over:** monolith ADR-002

## Context

Couriers send frequent updates (checkpoints, location pings, delivery attempts), often
retried over bad networks and sometimes out of order. The brief lists a NoSQL database in the
required stack.

## Decision

- The **tracking** service stores one `journeys` document per shipment (`_id` = shipment id)
  plus an append-only `tracking_events` collection in **MongoDB**.
- Every update carries a client-generated **`event_id`**, used as the event's `_id`, so a
  retry is a no-op; the timeline is ordered by `occurred_at`, not arrival time. The journey is
  moved with a conditional single-document update that only goes forward in time, so late
  updates join the timeline without regressing the current milestone. No multi-document
  transactions are needed.
- Devices may send a **batch** after being offline; each item gets its own result
  (`recorded`, `duplicate`, `rejected` with a code). Couriers may only report on journeys
  assigned to them; timestamps too far in the future are rejected.
- **Each event is its own outbox entry** (`published: false`); the tracking worker's relay
  claims unpublished events with a lease and publishes `tracking.milestone_recorded`. The
  orchestrator forwards field milestones to the delivery workflow (ADR-010). Tracking never
  writes shipment status itself.
- A public, unauthenticated lookup by reference number exposes only the milestone timeline.

## Consequences

- High-volume writes stay off the transactional PostgreSQL databases.
- The journey is a read model: shipment status remains authoritative in the shipment service.
- Tests need a MongoDB-compatible server on `MONGO_URL` (MongoDB 7 in Compose).
