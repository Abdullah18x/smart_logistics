# ADR-003: RS256 access tokens verified locally with JWKS

**Status:** Accepted · **Date:** 2026-10-06 · **Replaces:** HS256 shared secret (monolith ADR-009)

## Context

With one service per process, a shared HMAC secret would have to be copied into every
service, and any of them could mint tokens. Calling Identity on every request would make it a
single point of failure and add a network hop to every call.

## Decision

- **Identity alone holds the private key** (a mounted secret, `infra/keys/jwt-private.pem`
  locally) and signs short-lived **RS256** access tokens (15 minutes).
- It publishes the public key at **`/.well-known/jwks.json`**, with a `kid`.
- Every other service fetches the JWKS at start-up (best effort), caches it in memory for an
  hour and verifies tokens **locally**: signature, issuer, audience, expiry. An unknown `kid`
  triggers a rate-limited refetch, which is how key rotation works.
- Claims carry everything authorisation needs: `sub`, `role`, `warehouse_ids`, `courier_id`,
  `name`. A route opens by **role**; a query is narrowed by **scope** (an operator sees only
  their warehouses).
- Refresh tokens are opaque, stored hashed, and **rotate**; replaying a rotated token revokes
  the user's sessions. Accounts lock after 5 failed logins for 15 minutes.
- When one service calls another for a user, it **forwards the caller's token**, so the callee
  applies its own permission checks.

## Consequences

- No service calls Identity on the request path; Identity being down stops new logins only.
- A pod that restarts simply fetches the JWKS again; there is no shared cache to lose.
- A role change or deactivation takes effect when the current access token expires (at most
  15 minutes). A Redis denylist for immediate revocation is planned for Week 3.
- Service-to-service calls with no user behind them (the Week 2 orchestrator) need their own
  identity: Identity will issue short-lived `system` tokens (planned).
