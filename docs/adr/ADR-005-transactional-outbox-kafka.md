# ADR-005: Transactional outbox to Kafka, at-least-once with consumer dedupe

**Status:** Accepted · **Date:** 2026-10-06 · **Carries over:** monolith ADR-003

## Context

Writing to the database and then publishing to Kafka is a dual write: a crash in between
loses the event, or publishes an event for a change that rolled back.

## Decision

- A service never publishes directly. It stages the event in an **`outbox_events`** row in
  the **same transaction** as the change, so both commit or neither does.
- Each service's **worker process** runs an `OutboxRelay` that claims pending rows with
  `FOR UPDATE SKIP LOCKED` (several relay pods work on different rows), publishes them in
  `seq` order (assigned at insert) with an idempotent producer (`acks=all`), and marks them
  sent. With one relay, one aggregate's events go out in the order they were written; with
  several, ordering per aggregate is not guaranteed, which is why consumers are idempotent and
  replicas apply newest-wins (ADR-006).
- **One topic per producing service** (`identity.events`, `warehouse.events`,
  `inventory.events`, `shipment.events`), keyed by aggregate id so one entity's events stay in
  order. Consumers filter on `event_type`.
- Delivery is **at-least-once**. Consumers record each event id in **`processed_events`** in
  the same transaction as their effect, so a redelivered event is a no-op on any pod.
- Events are JSON envelopes (`event_id`, `event_type`, `version`, `occurred_at`, `producer`,
  `payload`). Avro with a Schema Registry is deferred to Week 3 (ADR-015).
- If Kafka is unreachable the worker retries with backoff instead of exiting; events wait in
  the outbox.

## Consequences

- No event is lost and none is published for a rolled-back change.
- Consumers must be idempotent, which the dedupe table makes mechanical.
- Events are eventually published (normally well under a second); readers of replicas see
  changes with that lag.
- A message whose handler keeps failing gets 5 attempts, then is logged and skipped (its offset
  is committed), so it needs a manual replay; the dead-letter topic in Week 3 replaces this.
