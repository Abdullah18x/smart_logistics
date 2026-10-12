# ADR-004: Synchronous calls only where an answer is needed now; reserve, then record

**Status:** Accepted · **Date:** 2026-10-06

## Context

Creating a shipment must not succeed without stock, and must not hold stock for a shipment
that was never created. The stock lives in inventory, the shipment in shipment: two databases.

## Decision

- **REST is used only when the caller cannot proceed without the answer.** Today there are two
  such calls: shipment → inventory to reserve/release stock, and identity → warehouse to
  validate an operator's warehouse assignment. Everything else travels as events.
- Shipment creation is **reserve, then record**:
  1. Shipment checks the origin warehouse in its local replica (no call).
  2. It asks inventory to **hold** the stock. Inventory locks the rows in id order and either
     holds everything or answers `409 insufficient_stock`.
  3. It inserts the shipment, its lines (SKU snapshots returned by inventory), the first
     history row and a `shipment.created` outbox row, in **one transaction**.
  4. If step 3 fails, it **releases the hold** at once (compensation).
  5. Inventory **confirms** the hold when it consumes `shipment.created`. Unconfirmed holds
     expire after 30 minutes, so a crash between steps 2 and 3 cannot leak stock (ADR-009).
- With an `Idempotency-Key`, the shipment id is derived from the key (`uuid5(owner:key)`), so a
  retry reuses the same hold instead of reserving twice.
- All service calls go through one `ServiceClient`: 3-second timeouts, up to 2 retries **only**
  for reads or keyed writes, a circuit breaker (opens after 5 consecutive failed attempts, lets
  calls through again after 30 s), and the callee's 4xx/5xx answers passed through unchanged so
  the client sees inventory's own `409`; 502/503/504 and timeouts become
  `503 upstream_unavailable`.

## Consequences

- Shipment creation depends on inventory being up; with the breaker open it fails fast with
  `503` instead of piling up requests.
- No distributed transaction and no two-phase commit; correctness rests on idempotent steps,
  compensation and expiry.
- The Week 2 dispatch workflow generalises this pattern into a Temporal saga (ADR-010).
