# ADR-015: Event contracts in Schema Registry (JSON Schema), and dead-letter topics

**Status:** Accepted · **Date:** 2026-11-10 · **Supersedes:** the 2026-10-06 version of this
ADR ("JSON envelopes now, Avro with Schema Registry in Week 3")

## Context

The brief names Kafka with a Schema Registry. The first version of this ADR deferred the
registry to Week 3 and assumed Avro. By Week 3 seven services publish 30 event types as JSON
envelopes, and two more services are about to consume all of them.

Two problems needed solving: **contracts** — nothing stopped a producer from changing a
payload in a way that breaks its consumers — and **poison events** — a consumer that could
not handle an event after its retries logged it and skipped it, so it was lost to anyone not
reading the logs.

## Decision

**Contracts.** Every event type has a JSON Schema contract, owned by the producing service
(`<service>/contracts.py`, built with small helpers in `sl_platform.contracts`). They are
enforced in three places:

1. **At staging** (`add_event`): the payload is checked against the local contract; a
   violation raises and the business transaction rolls back. This runs in every test.
2. **At publish** (outbox relay): the contract is registered in Confluent Schema Registry
   under `<topic>-<event_type>` with **BACKWARD** compatibility, and the envelope is stamped
   with the `schema_id`. The registry refuses a breaking change; the event stays in the
   outbox until it is fixed.
3. **At consume**: the payload is checked against the schema its id points to. A violation
   goes to the dead-letter topic without being handled.

Contracts allow extra properties: adding an optional field needs no consumer release;
removing a field or changing its type is what the registry rejects.

**JSON Schema, not Avro.** The registry supports both. JSON Schema gives the same governance
— registered contracts, compatibility checks, schema ids — while the envelope stays readable
JSON (greppable in Kafka UI and logs) and the 30 existing event types need no re-encoding.
The schema id travels in the envelope (`schema_id`) rather than Confluent's binary
wire-format prefix, for the same reason.

**Availability.** A consumer never refuses an event because the *registry* is down: the
producer already validated it, so the event is handled and counted
(`events_unvalidated_total`). A producer, on the other hand, does not publish without
registering: the outbox holds events until the registry is back.

**Dead letters.** A consumer that gives up on an event — its handler failed every retry, the
message is unreadable, or the payload breaks its contract — publishes the original bytes to
`dlq.<consumer>` with headers saying why, then commits its offset so the partition keeps
moving. `./dev.sh dlq list` shows them; `./dev.sh dlq replay <consumer>` sends them to
`retry.<consumer>`, a topic only that consumer reads, so services that handled the event the
first time are not involved. Consumer dedupe makes a repeated replay harmless. Each consumer
creates its retry topic before subscribing, so a replay is picked up immediately.

The Schema Registry is optional at runtime (unset `SCHEMA_REGISTRY_URL`): tests and a minimal
local setup still validate every staged event against its local contract.

## Consequences

- A producer bug that changes a payload fails in that producer's own tests, before anyone
  else sees it.
- An incompatible contract change fails at deploy (first publish), not in a consumer.
- No event is silently dropped: every give-up is on a dead-letter topic, counted in
  `dead_letters_total`, and replayable.
- A registry outage pauses publishing (events wait in the outbox, the backlog alert fires);
  consumption continues.
- Contracts are Python, so they are reviewed with the code that emits the events; the
  registry holds the JSON Schema each produces.
