# ADR-017: Service accounts for service-to-service calls

**Status:** Accepted · **Date:** 2026-10-12

## Context

The orchestrator calls shipment, inventory, courier and tracking on behalf of no user: a
workflow may run hours after the request that started it, long after any user token has
expired. Every service already verifies RS256 tokens locally (ADR-003), and some actions
(marking a shipment dispatched or delivered) must be reserved to the workflows.

## Decision

- A non-human role, **`system`**, exists in the shared role enum. It can never be given to a
  user: the identity API rejects it on create and update.
- Identity issues short-lived tokens (5 minutes by default) for **client credentials** at
  `POST /api/v1/auth/service-token`. Clients and their secrets come from configuration
  (`SERVICE_CLIENTS`, a JSON object); the secret is compared in constant time. The token's
  subject is a stable uuid5 of the client id, so audit trails name the same actor every time.
- `ServiceTokenProvider` (platform library) caches the token and renews it 60 s before
  expiry; `ServiceClient` uses it when no user token is passed and renews once on a 401.
- Services authorise `system` explicitly where it is needed. Post-dispatch shipment statuses
  (`dispatched`, `dispatch_failed`, `picked_up`, `in_transit`, `out_for_delivery`,
  `delivered`, `delivery_failed`) may only be set by `system`, so the workflows are their
  single writer.

## Consequences

- No user token is ever stored or replayed by background work.
- Rotating a secret means updating `SERVICE_CLIENTS` in identity and the client's
  `SERVICE_CLIENT_SECRET`; tokens already issued expire within minutes.
- A secrets manager replaces the environment variables in production (Week 4 hardening).
