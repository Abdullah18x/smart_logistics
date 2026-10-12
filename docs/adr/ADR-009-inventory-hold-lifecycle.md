# ADR-009: Inventory hold lifecycle

**Status:** Accepted · **Date:** 2026-10-06

## Context

Phase 1 holds expired after a fixed time, so a shipment that waited long enough for dispatch
lost its stock, and dispatching it then failed or oversold.

## Decision

```
reserve ──▶ HELD (unconfirmed, expires in 30 min)
              │  shipment.created
              ▼
           HELD (confirmed, never expires)
              │                       │
   status → dispatched          status → cancelled
              ▼                       ▼
          COMMITTED               RELEASED
   (on_hand and reserved      (reserved decreases)
    both decrease; ledger row)
```

- A hold is **unconfirmed** until inventory consumes `shipment.created`; only unconfirmed holds
  expire, and they are reclaimed by `POST /inventory/release-expired` (a scheduled sweeper
  arrives with Celery in Week 3).
- **Commit** happens on `shipment.status_changed` → `dispatched`; **release** on
  `cancelled`. Both are idempotent. Commit writes an `outbound_dispatch` stock-ledger row
  (on-hand changes); release only returns reserved quantity and publishes `stock.released`.
- DISPATCHED → CANCELLED is not a transition: once goods leave, getting them back is a
  return, not a cancellation (phase 1 allowed it and lost the stock).

## Consequences

- A recorded shipment keeps its stock however long it waits for dispatch.
- Stock for a shipment that was never recorded comes back within 30 minutes.
