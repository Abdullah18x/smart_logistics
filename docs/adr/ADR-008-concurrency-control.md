# ADR-008: Concurrency control

**Status:** Accepted · **Date:** 2026-10-06 · **Carries over:** monolith ADR-013, fixed

## Decision

- **Shipments: optimistic locking.** `version` is SQLAlchemy's `version_id_col`, so every
  UPDATE carries `WHERE version = <read value>`. A concurrent change gets
  `409 concurrent_update` instead of silently overwriting. (Phase 1 incremented the column by
  hand and checked nothing.)
- **Stock: pessimistic row locks with one lock order.** Reservations lock inventory rows
  first, ordered by id, then holds. Two reservations for overlapping SKUs serialise; they can
  never deadlock.
- **Invariants live in the database.** Check constraints make negative stock and
  over-reservation impossible whatever the code above does
  (`reserved_qty <= on_hand_qty`, `on_hand_qty >= 0`); `available_qty` is a computed column.
- **One commit per request.** Services only flush; the endpoint commits once, so a failure
  rolls back the whole operation, including its outbox rows.

## Consequences

- Last-unit races are decided by the database: the first transaction wins, the second gets
  `409 insufficient_stock`.
- Clients must handle `409 concurrent_update` by re-reading and retrying.
