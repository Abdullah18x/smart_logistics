# ADR-012: One edge gateway and one API portal

**Status:** Accepted · **Date:** 2026-10-08

## Decision

- **Traefik** is the single entry point on `:8000`, routing by path prefix
  (`/api/v1/auth`, `/users` → identity; `/warehouses` → warehouse; `/inventory`, `/skus` →
  inventory; `/shipments` → shipment). Clients never address a service directly.
- The same routes exist in both run modes: in Docker via container labels, and in local
  development via `infra/gateway/local.yml`, which forwards to the services on the host.
- **One Swagger UI** for every service sits behind the gateway at `/docs`, loading each
  service's own `/openapi.json` live; a token authorised once is reused across services.
- `./dev.sh docs` regenerates a merged OpenAPI file, a Postman collection, curl scripts and an
  offline HTML reference from the code.

## Consequences

- Clients see one API even though four services serve it.
- Authentication stays in the services (ADR-003); the gateway does no token checks, so it can
  be swapped (Kong, Envoy, a cloud load balancer) without code changes.
