# ADR-002: Service boundaries

**Status:** Accepted · **Date:** 2026-10-06

## Context

Boundaries decide which operations stay inside one transaction. A boundary drawn through a
consistency rule ("stock never goes negative", "a shipment's lines match its holds") turns a
local constraint into a distributed protocol.

## Decision

| Service | Owns | Why it is separate |
|---|---|---|
| **identity** | Users, sessions, operator↔warehouse scopes, the signing key | Security-sensitive; every other service only needs its public key |
| **warehouse** | Facilities, zones, operating hours | Reference data with few writes and many readers |
| **inventory** | SKU catalog, stock positions, holds, stock ledger | Stock invariants must be enforced in one database; the catalog lives here because only stock and line snapshots read it |
| **shipment** | Shipments, items, packages, addresses, status history | The aggregate the business revolves around |
| **courier** (Week 2) | Courier profiles, availability, assignments | Changes with fleet operations, not with accounts |
| **orchestrator** (Week 2) | Dispatch and delivery workflows (Temporal) | Long-running processes, no tables of its own |
| **tracking** (Week 2) | Tracking journeys (MongoDB) | High-volume, append-only writes |

A courier is **not** just a user with `role = courier`: the profile (vehicle, capacity,
availability) belongs to the courier service, and the user row carries only `courier_id`.

## Consequences

- Every stock rule is a database constraint in one place (inventory); shipments ask for
  stock instead of computing it.
- Shipment creation spans two services; this is the one synchronous dependency (ADR-004).
- Notifications and analytics (Week 3) and the AI assistant (Weeks 4–5) attach as consumers
  without changing these services.
