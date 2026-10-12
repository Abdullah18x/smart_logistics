# ADR-007: Layered idempotency

**Status:** Accepted · **Date:** 2026-10-06 · **Carries over:** monolith ADR-006, fixed

## Context

Clients retry on timeouts, Kafka redelivers after rebalances, and with several pods the same
request can arrive twice at the same moment on different machines.

## Decision

Each layer is idempotent on its own:

| Layer | Mechanism |
|---|---|
| HTTP writes | `Idempotency-Key` header. Stored per **(owner, key)**, so two users never collide. A first request **claims** the key with a 60-second lease; a concurrent duplicate gets `409 request_in_progress`; the stored response is replayed for 24 hours; a different body with the same key is `422`. The response is recorded **in the same transaction** as the work, and the claim is released if the work fails. |
| Entity creation | Keyed shipment ids are deterministic (`uuid5(owner:key)`), so even a retry that misses the stored response cannot create a second shipment. |
| Stock holds | One hold per `(shipment_id, sku_id)` (unique constraint); repeating a reserve for the same shipment returns the existing holds. |
| Events | `processed_events` per consumer (ADR-005); Kafka idempotent producer. |
| Workflows (Week 2) | One Temporal workflow per shipment (`dispatch-{shipment_id}`); activities keyed by workflow id. |

## Consequences

- A retry is always safe; a double-click cannot create two shipments or hold stock twice.
- The phase-1 bugs (keys shared across users, response stored after commit) are fixed.
