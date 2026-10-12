# ADR-014: Soft deletion and an immutable audit trail

**Status:** Accepted · **Date:** 2026-10-06 · **Carries over:** monolith ADR-016

## Decision

- Entities a user can delete and might regret carry `deleted_at` / `deleted_by`: users,
  warehouses, zones, SKUs, shipments. Queries exclude them by default; restore endpoints bring
  them back. Deletion is published as an event so other services react (ADR-006).
- Ledgers are never updated or deleted: `stock_movements` (every stock change) and
  `shipment_status_history` (every transition, with actor, role and reason).
- Rows with their own lifecycle (holds, refresh tokens) and children that only exist with
  their parent are not soft-deleted.

## Consequences

- "Shipment history and audit trail" from the brief is a query, not a reconstruction.
- Unique constraints on soft-deletable rows must account for deleted rows.
