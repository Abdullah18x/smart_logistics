# ADR-010: Temporal for dispatch and delivery orchestration

**Status:** Accepted, implementation in Week 2 · **Date:** 2026-10-12 · **Carries over:** monolith ADR-004/005

## Context

Dispatch touches five services (validate shipment, confirm stock, generate labels, assign a
courier, start tracking) and must not leave a shipment half-dispatched. Delivery runs for
hours or days, waits on courier input and needs timers (no pickup, failed attempts).

## Decision

- An **orchestrator** service runs Temporal workers; Temporal persists workflow state, so a
  crash resumes exactly where it stopped.
- **DispatchWorkflow**, one per shipment (`dispatch-{shipment_id}`; Temporal rejects a second
  run with the same id): validate → confirm hold → generate labels → assign courier →
  initialise tracking → mark DISPATCHED. Each activity retries on its own and is idempotent.
  A permanent failure runs compensations in reverse (release the courier, mark
  DISPATCH_FAILED with the reason); the hold is kept so a retry can reuse it.
- **DeliveryWorkflow** starts after dispatch and stays open until a terminal state. Courier
  updates arrive as **signals** (via the tracking service); the workflow validates each
  transition against the state machine and is the **only writer** of post-dispatch shipment
  status. Timers escalate a missed pickup and start a return after the third failed attempt.
- Clear boundary: **Temporal** for multi-step processes with state, **Kafka** for facts
  others react to, **Celery** (Week 3) for fire-and-forget jobs and schedules.

## Consequences

- "Partial failures must not corrupt shipment state" holds by construction.
- Workflow code must be deterministic; I/O lives in activities.
- Adds a Temporal server (and its UI on :8233) to the stack.
