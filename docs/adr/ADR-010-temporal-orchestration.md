# ADR-010: Temporal for dispatch and delivery orchestration

**Status:** Accepted, implemented in Week 2 · **Date:** 2026-10-12 · **Carries over:** monolith ADR-004/005

## Context

Dispatch touches five services (validate shipment, confirm stock, generate labels, assign a
courier, start tracking) and must not leave a shipment half-dispatched. Delivery runs for
hours or days, waits on courier input and needs timers (no pickup, failed attempts).

## Decision

- An **orchestrator** service (no database of its own) runs the Temporal worker; Temporal
  persists workflow state, so a crash resumes exactly where it stopped. Its activities call
  the other services over HTTP as the `orchestrator` **service account** (ADR-017).
- Dispatch is **started by an event**, not a direct call: `POST /shipments/{id}/dispatch`
  moves the shipment to `dispatching` and stages `shipment.dispatch_requested` in the same
  transaction (outbox). The orchestrator's trigger consumes it and starts the workflow, so a
  request is never lost between "accepted" and "started".
- **DispatchWorkflow**, one per dispatch *attempt*: id `dispatch-{shipment_id}-{attempt}`,
  started with `REJECT_DUPLICATE`, so a redelivered event cannot run an attempt twice while a
  retry after `dispatch_failed` gets a fresh id. Steps: validate → confirm hold → generate
  labels → assign courier (waits durably and retries when nobody is free) → open the tracking
  journey → mark DISPATCHED. Each activity retries on its own and is idempotent. A business
  "no" (no hold, no courier) is non-retryable and runs the compensations in reverse (close
  the journey, release the courier, mark DISPATCH_FAILED with the reason); the hold is kept
  so a retry can reuse it. An attempt that is no longer the shipment's current one is
  skipped without touching anything.
- **DeliveryWorkflow** (`delivery-{shipment_id}`) is started by the dispatch workflow as a
  child that outlives it (parent close policy ABANDON) and stays open until delivered or
  returned. Courier milestones arrive as **`field_update` signals** forwarded by the trigger
  from `tracking.milestone_recorded`. The workflow ignores duplicates and stale reports,
  fills gaps left by a device that was offline (walks `picked_up → in_transit` before
  `out_for_delivery`), and is the **only writer** of post-dispatch shipment status (enforced:
  only the `system` role may set those statuses). Timers reassign an overdue pickup once and
  start a return after the last allowed failed attempt.
- Clear boundary: **Temporal** for multi-step processes with state, **Kafka** for facts
  others react to, **Celery** (Week 3) for fire-and-forget jobs and schedules.

## Consequences

- "Partial failures must not corrupt shipment state" holds by construction.
- Workflow code must be deterministic; I/O lives in activities.
- Adds a Temporal server (PostgreSQL-backed) and its UI on :8233 to the stack; workflow
  tests need the Temporal CLI dev server (`TEMPORAL_CLI_PATH` or the SDK download).
