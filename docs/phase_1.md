# Week 1 — Foundation & Core Services (microservices)

**Branch:** `microservices` · **Period:** 2026-10-06 → 2026-10-12
**Result:** four independently deployable services with isolated databases, running locally
on macOS, Linux and Windows and in Docker; 115 tests passing.

The first Week 1 build was a modular monolith; its build log is this file on the `phase_1`
branch. The requirement then changed to a true microservice architecture, so Week 1 was
rebuilt from that code base. This log records the rebuild.

---

## 1. Scope (from the execution plan)

| Week 1 scope | Delivered |
|---|---|
| Initial architecture and project setup | uv workspace monorepo, shared platform library, import-linter boundaries, [16 ADRs](adr/) |
| Shipment management APIs | shipment service: shipments, items, packages, addresses, status transitions, audit trail |
| User roles and auth basics | identity service: four roles, RS256 tokens verified locally by every service, refresh rotation, lockout, warehouse scopes |
| Warehouse schema design | warehouse service: warehouses, zones, operating hours; replicas in inventory and shipment |
| Local environment setup | `./dev.sh` / `dev.ps1` / `dev.cmd`; hot reload; one Swagger UI for every service |

| Deliverable | Evidence |
|---|---|
| Working APIs | 56 endpoints through one gateway (`docs/curl/openapi.json`) |
| Database schema ready | One Alembic chain per service; [database.md](database.md) generated from the models |
| Docker setup operational | `docker-compose.yml`: 4 databases, Kafka, gateway, Swagger UI, Jaeger, 4 APIs + 4 workers |
| Clean service structure | Same layout in every service; services never import each other (enforced) |

## 2. What each service does

| Service | Highlights |
|---|---|
| **identity** | Login with constant-time password check and lockout (5 attempts / 15 min); refresh tokens stored hashed, rotated, replay revokes all sessions; RS256 signing and `/.well-known/jwks.json`; users CRUD with soft delete and restore; operator↔warehouse scopes validated against the warehouse service; drops scopes on `warehouse.deleted` |
| **warehouse** | Warehouses with inline address and two coordinate pairs, zones, weekly hours; status changes; courier-only `/locations` view; every change published as a full snapshot |
| **inventory** | SKU catalog; stock positions with generated `available_qty` and check constraints; adjustments with an append-only ledger; reservation API (hold all lines or none, confirm, commit, release); unconfirmed holds expire, confirmed ones never; locks in id order |
| **shipment** | Create = reserve stock in inventory, then record in one transaction, compensating on failure; deterministic ids for keyed requests; packages; state machine with role table; optimistic locking; status history; warehouse replica |
| **platform** (`libs/sl-platform`) | Token verification, app factory, error mapping, idempotency store, outbox and relay, consumer with dedupe, service client with circuit breaker, worker runner, telemetry, test helpers |

## 3. Defects fixed from the Phase 1 code review

The Phase 1 code review found eight bugs; each is fixed in the rebuild and
covered by a test.

| # | Phase 1 defect | Fix |
|---|---|---|
| 1 | A failed keyed create left an orphan shipment | Idempotency claim and response are part of the request's single transaction; failure rolls back everything and releases the claim |
| 2 | An expired hold let a shipment dispatch without reducing stock | Holds are confirmed when the shipment is recorded and then never expire ([ADR-009](adr/ADR-009-inventory-hold-lifecycle.md)) |
| 3 | `Shipment.version` was not enforced | Real `version_id_col`; concurrent change → `409 concurrent_update` |
| 4 | Operator could bypass scope on `release-expired` | Scope checked for every `warehouse_id`, given or not |
| 5 | Idempotency keys global, never expiring, no stale-claim takeover, replay returned 200 | Keys per (owner, key), 24 h expiry, 60 s lease takeover, original status replayed |
| 6 | Inconsistent lock order, deadlocks possible | Items first in id order, then holds, everywhere |
| 7 | `DISPATCHED → CANCELLED` lost stock | Transition removed; post-dispatch is a return |
| 8 | Drifted state-machine constants | Single declaration; derived sets computed from it |
| — | No shipment status history | `shipment_status_history`, one row per transition with actor, role, reason |
| — | Couriers saw full warehouse records | Couriers get the narrower `/warehouses/locations` view |
| — | Every service method committed | Services flush; the endpoint commits once |

## 4. Defects found during the rebuild

| Found | Cause | Fix |
|---|---|---|
| Shared metadata collided on `warehouse_refs` when all services loaded in one process | One declarative base for every service | `new_base()` per service plus a `PlatformBase` |
| FastAPI read the role-gate parameter as a request body | Postponed annotations on a closure | `Depends(self)` default |
| Shipment migration downgrade left a sequence and enums | Hand-written DDL not mirrored | Downgrade drops them; `alembic check` per service in `./dev.sh check` |
| `shipment-worker` exited immediately | `worker.py` committed empty | Worker written; `test_worker.py` in every service starts each worker |
| A missing Kafka crashed every worker and stopped the dev stack | Producer and consumer started once, no retry | Workers retry with backoff (outbox holds events); `start` checks infrastructure first |
| `./dev.sh migrate` failed with `No module named 'identity'` | A virtualenv left by the monolith was reused | Wrappers run `uv sync --all-packages` before every command |
| Full-Docker gateway answered 404 everywhere | Traefik v3.5's Docker provider rejected by Docker Engine 29 | Traefik v3.6 |

## 5. Verification

```
./dev.sh test     platform 12 · identity 34 · warehouse 17 · inventory 19 · shipment 24 · e2e 9  = 115
./dev.sh check    ruff, format, import-linter (2 contracts kept), alembic check ×4
```

All suites run against real PostgreSQL built from the migrations. The e2e suite loads all four
services in one process, each on its own database, joined by an in-memory event bus, and
proves they cooperate only through HTTP and events: token accepted everywhere, replicas built
from events, create → dispatch moves stock, idempotent retry holds stock once, insufficient
stock reaches the client, cancellation releases the hold, scopes apply in every service,
warehouse changes propagate.

Run by hand: services on the host with the Docker infrastructure (`infra up`, `migrate`,
`seed`, `start --reload`), through the gateway and Swagger UI.

## 6. Not carried over, or deferred

| Item | Where it goes |
|---|---|
| Courier tables (`couriers`, `courier_assignments`) from the monolith | Courier service, Week 2 |
| `password_reset_tokens` (table only; no endpoints in Phase 1) | Identity, when password reset is scheduled |
| Kubernetes manifests | Per-service manifests after Week 3; the monolith's are on `phase_1` (`deploy/k8s`) |
| Test depth: Phase 1 had ≈700 tests, the rebuild 115 | Every Week 2 feature lands with unit, workflow and e2e tests; coverage gate in Week 3 |
| Hold sweeper on a schedule | Celery beat, Week 3 (`POST /inventory/release-expired` meanwhile) |
| Dead-letter topic, Schema Registry | Week 3 |
| Prometheus, Grafana, trace context through Kafka | Week 3 |
| Immediate token revocation | Redis denylist, Week 3 |
