# ADR-011: MongoDB for tracking journeys

**Status:** Accepted, implementation in Week 2 · **Date:** 2026-10-12 · **Carries over:** monolith ADR-002

## Context

Couriers send frequent updates (checkpoints, location pings, delivery attempts), often
retried over bad networks and sometimes out of order. The brief lists a NoSQL database in the
required stack.

## Decision

- The **tracking** service stores one journey document per shipment plus an append-only
  event collection in **MongoDB**.
- Every update carries a client-generated **`event_id`** with a unique index, so retries are
  no-ops; events are ordered by `occurred_at`, not arrival time.
- Tracking forwards milestone updates to the delivery workflow (ADR-010) and publishes
  `tracking.*` events; it never writes shipment status itself.

## Consequences

- High-volume writes stay off the transactional PostgreSQL databases.
- The journey is a read model: shipment status remains authoritative in the shipment service.
