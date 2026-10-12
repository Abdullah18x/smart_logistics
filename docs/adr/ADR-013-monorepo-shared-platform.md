# ADR-013: One repository, a shared platform library, enforced boundaries

**Status:** Accepted · **Date:** 2026-10-06

## Decision

- A **uv workspace**: `libs/sl-platform` plus one package per service, locked together in one
  `uv.lock`.
- `sl-platform` holds **plumbing only**: auth verification, database helpers, idempotency,
  outbox and consumer, service client, app factory, worker runner, telemetry, test helpers.
  It holds no business rules.
- **import-linter** enforces in CI that services never import each other and that the
  platform imports no service. Data crosses only over HTTP or Kafka.
- One `service.Dockerfile` with `ARG SERVICE` builds every image; an API and its worker run
  from the same image with different commands, so they scale independently.

## Consequences

- One pull request can change a contract and both sides of it, while runtime coupling stays
  impossible.
- A platform change redeploys every service; it is kept small and versioned with care.
