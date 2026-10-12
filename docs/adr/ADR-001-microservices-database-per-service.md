# ADR-001: Microservices with a database per service

**Status:** Accepted · **Date:** 2026-10-06 · **Supersedes:** monolith ADR-001 (modular monolith) and ADR-008 (schema per module), both on the `phase_1` branch

## Context

Phase 1 was a modular monolith: one FastAPI app, one PostgreSQL database with a schema per
module, real foreign keys across schemas. The requirement changed to a true microservice
architecture: each service deployable on its own, owning its data, with no shared database.

The domain has clear seams (identity, facilities, stock, shipments) and very different load
profiles: courier tracking updates will outnumber shipment writes by orders of magnitude, and
dispatch is a long-running workflow, not a request.

## Decision

- Each service is its own process, image and **isolated PostgreSQL instance** (its own
  container, volume and credentials). No service can open another's database.
- Data crosses a boundary only through the owner's **REST API** (for questions that need an
  answer now) or its **Kafka events** (for facts others react to). See ADR-004 and ADR-005.
- There are **no foreign keys between services**. A reference to another service's row is a
  plain id; integrity is kept by validation at write time and by events (ADR-006).
- Each service has its own Alembic migration chain and its own version history.

## Consequences

- A service can be deployed, scaled and migrated alone; a failing database takes down one
  service, not the platform.
- Cross-service joins are impossible. Reads that need another service's data use local
  replicas built from events (ADR-006); the cost is eventual consistency of those copies.
- Multi-service changes cannot be one ACID transaction. They become reservations with
  compensation (ADR-004, ADR-009) and, from Week 2, Temporal sagas (ADR-010).
- More moving parts to run locally; `./dev.sh` (ADR-016) hides that.

## Alternatives considered

- **Keep the modular monolith.** Simplest and consistent, but not what was asked for, and
  every module scales and fails together.
- **Shared database, separate schemas.** Easy joins, but services couple through tables and
  migrations, so it is a distributed monolith.
