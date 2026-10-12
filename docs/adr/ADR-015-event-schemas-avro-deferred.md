# ADR-015: JSON event envelopes now, Avro with Schema Registry in Week 3

**Status:** Accepted · **Date:** 2026-10-06 · **Revises:** monolith ADR-007

## Context

The brief names Kafka with a Schema Registry. With four services and a handful of event types,
introducing Avro before the event flows were proven would have slowed Week 1.

## Decision

- Events are JSON envelopes with an explicit `version` field and a `producer`.
- Payload changes are **additive only** until the registry arrives.
- Week 3 introduces the Schema Registry with Avro schemas and **BACKWARD** compatibility,
  registered per topic, and the envelope moves onto it.

## Consequences

- No compile-time contract between producer and consumer yet; consumer tests cover the shapes.
