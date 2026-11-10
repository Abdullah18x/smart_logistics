# ADR-023: Immediate token revocation with a Redis denylist

**Status:** Accepted · **Date:** 2026-11-10 · **Amends:** [ADR-003](ADR-003-token-verification-jwks.md)

## Context

Services verify access tokens locally (ADR-003); they live 15 minutes. Revoking refresh
tokens stops new access tokens but not the ones already issued: a suspended user, or one who
signed out everywhere after losing a phone, keeps access to every service except Identity
until expiry.

## Decision

- Identity writes a **denylist in Redis** that every service's `Auth` checks:
  - `sl:revoked:user:<id>` = revocation time in ms; any token of that user issued at or
    before it is refused. Written by `RefreshTokenRepository.revoke_all`, which every
    "end all sessions" path already goes through: sign-out everywhere, suspension,
    deactivation, role change, warehouse-scope change, refresh-token replay. Kept for 30
    minutes (longer than any access token).
  - `sl:revoked:jti:<jti>` — the one token used for a single-session sign-out, kept until
    it expires.
- Tokens carry `iat_ms` so a login a moment after a revocation is not caught by it.
- The check is one `MGET` with 250 ms timeouts. **Fail open** by default: if Redis is down,
  tokens are accepted on signature and expiry (logged, `token_denylist_checks_total{outcome=
  "unavailable"}`); `DENYLIST_FAIL_CLOSED=true` refuses instead. Without `REDIS_URL` the
  check is off (tests, minimal setups).
- The same Redis holds short-lived caches (`sl_platform.cache`), e.g. analytics `/summary`.
- The JWKS stays cached in each process (ADR-003); it needs no shared cache.

## Consequences

- Revocation takes effect on the next request in every service.
- One network round trip per authenticated request (sub-millisecond locally).
- Identity writes the denylist before its transaction commits: if the commit fails, the user
  just signs in again — the safe direction.
