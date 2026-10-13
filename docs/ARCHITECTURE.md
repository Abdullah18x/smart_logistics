# SmartLogistics — Technical Architecture

**Project:** SmartLogistics — Intelligent Supply Chain & Delivery Orchestration Platform
**Client:** TransFleet
**Component:** Backend platform (Part A) + GenAI layer (Part B)
**Status:** Week 2 complete (dispatch, courier assignment, tracking, delivery)
**Last updated:** 2026-10-13

> This document describes the **microservices** architecture on the `microservices` branch.
> The Phase 1 modular monolith and its architecture document live on the `phase_1` branch.

---

## Table of Contents

1. [Purpose and Scope](#1-purpose-and-scope) · [1.1 Requirement coverage](#11-requirement-coverage)
2. [Architectural Drivers](#2-architectural-drivers)
3. [System Context](#3-system-context)
4. [Services and Ownership](#4-services-and-ownership)
5. [Communication](#5-communication)
6. [Data Architecture](#6-data-architecture)
7. [Shipment Lifecycle and State Machine](#7-shipment-lifecycle-and-state-machine)
8. [Key Flows](#8-key-flows)
9. [Consistency and Idempotency](#9-consistency-and-idempotency)
10. [API and Security](#10-api-and-security)
11. [Observability](#11-observability)
12. [Dispatch and Delivery](#12-dispatch-and-delivery)
13. [Part B — GenAI Layer](#13-part-b--genai-layer)
14. [Runtime Topology](#14-runtime-topology)
15. [Repository Layout](#15-repository-layout)
16. [Architecture Decision Records](#16-architecture-decision-records)
17. [Non-Functional Targets](#17-non-functional-targets)
18. [Assumptions, Open Questions and Risks](#18-assumptions-open-questions-and-risks)
19. [Delivery Roadmap](#19-delivery-roadmap)

---

## 1. Purpose and Scope

This is the reference for service boundaries, data ownership, how services communicate,
consistency guarantees and observability. Product requirements, use cases and the
feature-to-deliverable traceability the brief asks for are in [PRD.md](PRD.md); the database
reference is [database.md](database.md); each significant decision has an ADR in [adr/](adr/).

**In scope:** shipment lifecycle, warehouse and inventory operations, dispatch orchestration,
courier assignment, tracking and delivery, returns, notifications, analytics, and the GenAI
assistant layer.

**Out of scope:** frontend applications, real carrier/EDI integrations, payments, production
cloud infrastructure, and route optimisation against live map data. External providers are
represented by mock adapters behind stable interfaces.

### 1.1 Requirement coverage

Every functional requirement in *SmartLogistics — Part A*, with its status at the end of
Week 2. Status means **built and verified by tests**, not designed.

| # | Requirement (Part A) | Where | Status |
|---|---|---|---|
| **1** | Shipment, package, address and metadata management | shipment service, [§7](#7-shipment-lifecycle-and-state-machine) | ✅ Built |
| | User registration with four roles | identity service, [§10](#10-api-and-security) | ✅ Built: role gate plus per-warehouse scoping, enforced in every service |
| | Courier profile management and availability | courier service, [§12.2](#122-courier-service) | ✅ Built: profiles, vehicle, capacity, weight limit, home city/warehouse, availability set by the courier, logins linked by event |
| | Shipment history and audit trail | `shipment_status_history`, `stock_movements` | ✅ Built: one immutable row per transition and per stock change |
| | Consistency across dependent systems | [§9](#9-consistency-and-idempotency) | ✅ Outbox events, replicas, reserve-then-record with compensation |
| **2** | Dispatch workflow: validate, reserve, label, assign, track, mark dispatched | [§12.1](#121-dispatch-workflow) | ✅ Built: event-started Temporal saga, PDF labels, automatic courier assignment with manual override |
| | Partial failures must not corrupt shipment state | [ADR-004](adr/ADR-004-synchronous-calls-reserve-then-record.md), [ADR-010](adr/ADR-010-temporal-orchestration.md) | ✅ Built: compensation on create; the dispatch saga undoes completed steps and records `dispatch_failed` with the reason |
| **3** | Delivery workflow: pickup, updates, attempts, confirmation | [§12.3](#123-delivery-workflow-and-tracking) | ✅ Built: `DeliveryWorkflow` driven by courier updates; gap filling, attempt counting, automatic return, overdue-pickup reassignment |
| | Idempotent retries, duplicate prevention | [ADR-007](adr/ADR-007-layered-idempotency.md) | ✅ `Idempotency-Key` per caller with leases, deterministic ids, consumer dedupe, workflow ids per attempt, device `event_id`s |
| | High-volume field updates, real-time visibility | tracking service, [§12.3](#123-delivery-workflow-and-tracking) | ✅ Built: MongoDB journeys, single and batch updates, out-of-order handling, public lookup by reference |
| **4** | Event-driven behaviours: traceable, recoverable, no double-processing | [§5.2](#52-asynchronous-kafka-events) | ✅ Kafka with transactional outbox and per-consumer dedupe; 🔴 dead-letter topic and Schema Registry Week 3 |
| **5** | Analytics metrics (nine listed) | analytics service | 🔴 Week 3 |
| **6** | Observability and failure diagnosis | [§11](#11-observability) | 🟡 Structured logs with request ids, health/readiness probes, OpenTelemetry traces to Jaeger; metrics and dashboards Week 3 |

**Tech stack**, as mandated by the brief:

| Layer | Required | Status |
|---|---|---|
| Backend | Python, FastAPI, PostgreSQL, NoSQL, Redis | ✅ Python 3.13, FastAPI, PostgreSQL 16 (one per service). MongoDB 7 (tracking). 🔴 Redis (token denylist, caching) Week 3 |
| Async | Celery + RabbitMQ, Kafka + Schema Registry, Temporal | ✅ Kafka (KRaft) with outbox; Temporal (dispatch and delivery). 🔴 Celery, RabbitMQ and Schema Registry Week 3 |
| Observability | Prometheus, Grafana, Jaeger, OpenTelemetry | ✅ OpenTelemetry → Jaeger. 🔴 Prometheus and Grafana Week 3 |
| DevOps | Docker, Docker Compose | ✅ One image recipe for every service, Compose stack, cross-platform dev CLI |

Nothing in the mandated stack is substituted; 🔴 items are scheduled ([§19](#19-delivery-roadmap)).

---

## 2. Architectural Drivers

| # | Driver | Source | Response |
|---|---|---|---|
| D1 | **Services must be independently deployable and own their data** | Manager requirement | Database per service, no cross-service foreign keys, boundaries enforced by import-linter ([ADR-001](adr/ADR-001-microservices-database-per-service.md), [ADR-013](adr/ADR-013-monorepo-shared-platform.md)) |
| D2 | **Partial failures must not corrupt state** | Dispatch workflow | Reserve-then-record with compensation; Temporal sagas for dispatch and delivery ([ADR-004](adr/ADR-004-synchronous-calls-reserve-then-record.md), [ADR-010](adr/ADR-010-temporal-orchestration.md)) |
| D3 | **Inventory must stay consistent** | Problem statement | All stock rules in one database, enforced by constraints and ordered row locks ([ADR-008](adr/ADR-008-concurrency-control.md), [ADR-009](adr/ADR-009-inventory-hold-lifecycle.md)) |
| D4 | **Events are delayed, duplicated or lost between systems** | Problem statement | Transactional outbox, at-least-once delivery, idempotent consumers ([ADR-005](adr/ADR-005-transactional-outbox-kafka.md), [ADR-007](adr/ADR-007-layered-idempotency.md)) |
| D5 | **One service's outage must not cascade** | Reliability goals | Local token verification, local replicas, timeouts and circuit breakers ([ADR-003](adr/ADR-003-token-verification-jwks.md), [ADR-006](adr/ADR-006-event-carried-state-replicas.md)) |
| D6 | **Peaks and high concurrent updates** | Scalability goals | API and worker processes scale separately; high-volume tracking off the transactional databases ([ADR-011](adr/ADR-011-mongodb-tracking.md)) |
| D7 | **Failures must be diagnosable across hops** | Observability expectations | Request ids, trace context on service calls, Jaeger; metrics and dashboards Week 3 |

---

## 3. System Context

```mermaid
flowchart TB
    client([Ops web · warehouse app · courier app · public tracking])
    gw[Gateway · Traefik :8000<br/>routes /api/v1/&lt;domain&gt; · Swagger UI at /docs]
    client --> gw

    subgraph services[Services — each with its own store]
        id[identity<br/>users · sessions · JWKS · service accounts]
        wh[warehouse<br/>facilities · zones · hours]
        inv[inventory<br/>SKUs · stock · holds · ledger]
        shp[shipment<br/>shipments · packages · labels · history]
        cr[courier<br/>couriers · assignments]
        trk[tracking<br/>journeys · field updates]
        orc[orchestrator<br/>Temporal worker + trigger]
    end
    gw --> id & wh & inv & shp & cr & trk & orc

    id --- iddb[(identity-db)]
    wh --- whdb[(warehouse-db)]
    inv --- invdb[(inventory-db)]
    shp --- shpdb[(shipment-db)]
    cr --- crdb[(courier-db)]
    trk --- mongo[(MongoDB)]
    orc --- temporal[[Temporal · own PostgreSQL]]

    shp -- "REST: reserve / release stock" --> inv
    id -- "REST: validate warehouse" --> wh
    orc -- "REST as system: dispatch steps,<br/>delivery status" --> shp & inv & cr & trk
    id -. "JWKS (public key, cached)" .-> wh & inv & shp & cr & trk & orc

    kafka{{Kafka · one topic per producer}}
    id & wh & inv & shp & cr & trk -- outbox relay --> kafka
    kafka -- "warehouse.* → replicas" --> inv & shp & cr
    kafka -- "shipment.created / status_changed" --> inv & cr
    kafka -- "courier.assigned" --> shp & trk
    kafka -- "courier.* → login link · warehouse.deleted" --> id
    kafka -- "dispatch_requested · milestone_recorded" --> orc
```

---

## 4. Services and Ownership

Each service is a FastAPI **API process** plus a **worker process** (outbox relay and Kafka
consumers), built from the same image and scaled separately.

| Service | API port | Database | Owns | Publishes | Consumes | Calls |
|---|---|---|---|---|---|---|
| **identity** | 8001 | identity-db :5441 | Users, refresh tokens, operator↔warehouse scopes, signing key, service clients | `user.*` | `warehouse.deleted` (drop scopes), `courier.*` (mirror `users.courier_id`) | warehouse (validate an assignment) |
| **warehouse** | 8002 | warehouse-db :5442 | Warehouses, zones, operating hours | `warehouse.*` (full snapshots) | — | — |
| **inventory** | 8003 | inventory-db :5443 | SKUs, stock positions, holds, stock ledger, warehouse replica | `stock.*` | `warehouse.*`, `shipment.created` (confirm hold), `shipment.status_changed` (commit / release) | — |
| **shipment** | 8004 | shipment-db :5444 | Shipments, items, packages, PDF labels, addresses, status history, warehouse replica | `shipment.*` incl. `dispatch_requested` | `warehouse.*`, `courier.assigned` / `unassigned` | inventory (reserve, release) |
| **courier** | 8005 | courier-db :5445 | Couriers, assignments, warehouse replica | `courier.*` incl. `assigned` / `unassigned` / `availability_changed` | `warehouse.*`, `shipment.status_changed` (complete / release assignments) | — |
| **orchestrator** | 8006 | — (state in Temporal) | `DispatchWorkflow`, `DeliveryWorkflow`, workflow progress API | — | `shipment.dispatch_requested`, `tracking.milestone_recorded` | shipment, inventory, courier, tracking (as `system`) |
| **tracking** | 8007 | MongoDB :27017 | Journeys, field updates (each its own outbox entry) | `tracking.milestone_recorded` | `courier.assigned`, `shipment.status_changed` (returns) | — |

Why the boundaries fall here is in [ADR-002](adr/ADR-002-service-boundaries.md). The
orchestrator holds no business data: it sequences other services' operations and keeps only
workflow state, which Temporal persists.

**Shared code without shared runtime.** `libs/sl-platform` holds plumbing only (auth
verification, database helpers, idempotency, outbox, consumer, service client, app factory,
worker runner, telemetry). import-linter fails the build if a service imports another service
or the platform imports a service.

---

## 5. Communication

### 5.1 Synchronous (REST)

Used only when the caller cannot continue without the answer
([ADR-004](adr/ADR-004-synchronous-calls-reserve-then-record.md)).

| Caller → callee | Call | Why it is synchronous |
|---|---|---|
| shipment → inventory | `POST /inventory/reservations`, `/{shipment_id}/release` | A shipment must not exist without its stock |
| identity → warehouse | `GET /warehouses/{id}` | An operator cannot be scoped to a warehouse that does not exist |
| every service → identity | `GET /.well-known/jwks.json` | At start-up (best effort), then when the cache is an hour old or a token has an unknown `kid`; never per request |
| orchestrator → identity | `POST /auth/service-token` | Client credentials for a 5-minute `system` token, cached and renewed 60 s before expiry |
| orchestrator activities → shipment, inventory, courier, tracking | validate, confirm hold, labels, assign / release / reassign courier, open / cancel journey, set status | Workflow steps; each call is idempotent and retried by Temporal ([§12](#12-dispatch-and-delivery)) |

Every call goes through `ServiceClient`: 3 s timeout; up to 2 retries, only for reads or writes
carrying an `Idempotency-Key`; a circuit breaker that opens after 5 consecutive failed attempts
and lets calls through again after 30 s (while open → `503 upstream_unavailable`); the
caller's bearer token (or the service token for background work, renewed once on a `401`) and
W3C `traceparent` forwarded. A callee's 4xx/5xx answer (for example
`409 insufficient_stock`) reaches the client unchanged; 502/503/504 and timeouts are retried
and then reported as `503 upstream_unavailable`.

### 5.2 Asynchronous (Kafka events)

Every event is written to the producing service's `outbox_events` table in the same
transaction as the change and relayed by its worker
([ADR-005](adr/ADR-005-transactional-outbox-kafka.md)). One topic per producer, keyed by
aggregate id so one entity's events stay ordered.

| Topic | Event types | Consumed by |
|---|---|---|
| `identity.events` | `user.created`, `.updated`, `.role_changed`, `.status_changed`, `.scope_changed`, `.deleted`, `.restored` | (analytics, notifications — Week 3) |
| `warehouse.events` | `warehouse.created`, `.updated`, `.status_changed`, `.deleted`, `.restored` — full snapshots | inventory, shipment (replicas), identity (`deleted`) |
| `inventory.events` | `stock.reserved`, `.committed`, `.released`, `.adjusted` | (analytics — Week 3) |
| `shipment.events` | `shipment.created`, `.updated`, `.status_changed`, `.dispatch_requested` | inventory (confirm / commit / release), courier (end assignments), tracking (returns), orchestrator (start dispatch) |
| `courier.events` | `courier.created`, `.updated`, `.status_changed`, `.availability_changed`, `.deleted`, `.restored`, `.assigned`, `.unassigned` | identity (login link), shipment (`courier_id`), tracking (journey courier) |
| `tracking.events` | `tracking.milestone_recorded` | orchestrator (signal the delivery workflow) |

Envelope: `event_id`, `event_type`, `version`, `occurred_at`, `producer`, `payload`.
Consumers record `event_id` in `processed_events` with their effect, so redelivery is a
no-op. Services without a SQL database (orchestrator, tracking) consume with a stateless
processor whose handlers are idempotent by themselves (Temporal's workflow-id rules, Mongo
`_id`s). A handler that keeps failing gets 5 attempts with backoff; after that the event is
logged with its id and **skipped** (its offset is committed), so it needs a manual replay. The
dead-letter topic in Week 3 replaces this. If Kafka is down, workers wait with backoff and
the outbox holds every event.

---

## 6. Data Architecture

**One PostgreSQL 16 instance per service**; no service holds another's credentials. Full
table reference, generated from the models: [database.md](database.md).

| Pattern | Where | Decision |
|---|---|---|
| **Plain-id references** | `warehouse_id`, `shipment_id`, `sku_id`, `courier_id` across services | No foreign keys between services ([ADR-001](adr/ADR-001-microservices-database-per-service.md)) |
| **Local replicas** | `warehouse_refs` in inventory, shipment and courier | Built from `warehouse.*` snapshots, newest-wins on `source_updated_at` ([ADR-006](adr/ADR-006-event-carried-state-replicas.md)) |
| **Snapshots** | `origin_warehouse_code`, `sku_code`, `sku_name`, unit weight on shipment lines | Copied at write time; reads never call another service |
| **Events instead of cascades** | `warehouse.deleted` → identity removes scopes | ([ADR-006](adr/ADR-006-event-carried-state-replicas.md)) |
| **Platform tables** | `outbox_events`, `processed_events`, `idempotency_keys` in every database | Events and retries without distributed transactions |
| **Soft deletion and ledgers** | users, warehouses, zones, SKUs, shipments; `stock_movements`, `shipment_status_history` | ([ADR-014](adr/ADR-014-soft-deletion-audit-trail.md)) |

Each service has its own Alembic chain; `./dev.sh check` runs `alembic check` per service so a
model change without a migration fails the build.

**MongoDB** holds tracking journeys and events
([ADR-011](adr/ADR-011-mongodb-tracking.md)); **Temporal's own PostgreSQL** holds workflow
state. Neither is shared with a service's transactional database.

---

## 7. Shipment Lifecycle and State Machine

Declared once, in `services/shipment/src/shipment/state_machine.py`; every transition is
validated there and recorded in `shipment_status_history` with actor, role and reason.

```mermaid
stateDiagram-v2
    [*] --> created
    created --> ready_for_dispatch
    created --> cancelled
    ready_for_dispatch --> dispatching
    ready_for_dispatch --> cancelled
    dispatching --> dispatched
    dispatching --> dispatch_failed
    dispatch_failed --> ready_for_dispatch
    dispatch_failed --> cancelled
    dispatched --> picked_up
    picked_up --> in_transit
    in_transit --> out_for_delivery
    out_for_delivery --> delivered
    out_for_delivery --> delivery_failed
    delivery_failed --> out_for_delivery
    delivery_failed --> return_initiated
    return_initiated --> returned
    delivered --> [*]
    returned --> [*]
    cancelled --> [*]
```

| Into status | Who may move it there |
|---|---|
| `ready_for_dispatch` | admin, warehouse operator |
| `dispatching` | nobody by `PATCH`: only `POST /shipments/{id}/dispatch` (admin, warehouse operator) |
| `dispatched`, `dispatch_failed` | `system` — the dispatch workflow |
| `picked_up`, `in_transit`, `out_for_delivery`, `delivered`, `delivery_failed` | `system` — the delivery workflow, from courier updates via tracking |
| `return_initiated` | admin, customer support, `system` (automatic after the last failed attempt) |
| `returned` | admin, warehouse operator |
| `cancelled` | admin, customer support |

- Contents are editable only in `created`, `ready_for_dispatch`, `dispatch_failed`.
- **No `dispatched → cancelled`**: goods that left the warehouse come back as a return
  (Phase 1 allowed it and lost the stock).
- Stock effects follow by event: `dispatched` commits the hold, `cancelled` releases it.
- Courier effects follow by event: `delivered`/`returned` complete the assignment,
  `cancelled`/`dispatch_failed` release it.

---

## 8. Key Flows

### 8.1 Authenticated request

```mermaid
sequenceDiagram
    participant C as Client
    participant G as Gateway
    participant I as identity
    participant S as shipment
    C->>G: POST /api/v1/auth/login
    G->>I: forward
    I-->>C: access token (RS256, 15 min) + refresh token
    Note over S: at start-up: GET identity /.well-known/jwks.json, cache in memory
    C->>G: GET /api/v1/shipments (Bearer token)
    G->>S: forward
    S->>S: verify signature, issuer, audience, expiry locally
    S->>S: role gate, then scope rows by warehouse_ids
    S-->>C: 200
```

No service calls identity per request; a restarted pod refetches the JWKS
([ADR-003](adr/ADR-003-token-verification-jwks.md)).

### 8.2 Create a shipment (reserve, then record)

```mermaid
sequenceDiagram
    participant C as Client
    participant S as shipment
    participant V as inventory
    participant K as Kafka
    C->>S: POST /shipments (Idempotency-Key)
    S->>S: claim key · check warehouse in local replica
    S->>V: POST /inventory/reservations (caller's token)
    V->>V: lock items by id · hold stock (unconfirmed, 30 min)
    V-->>S: holds + SKU snapshots (or 409 insufficient_stock)
    S->>S: one transaction: shipment, items, history, outbox row, stored response
    alt the transaction fails
        S->>V: release hold (compensation)
    end
    S-->>C: 201
    S--)K: relay shipment.created
    K--)V: confirm hold (never expires now)
```

### 8.3 Dispatch and delivery

```mermaid
sequenceDiagram
    participant C as Client
    participant S as shipment
    participant K as Kafka
    participant O as orchestrator
    participant V as inventory
    participant R as courier
    participant T as tracking
    C->>S: POST /shipments/{id}/dispatch
    S->>S: dispatching · attempt n · outbox: dispatch_requested
    S-->>C: 202 {dispatch_workflow_id}
    S--)K: shipment.dispatch_requested
    K--)O: start DispatchWorkflow(dispatch-{id}-{n})
    O->>S: validate
    O->>V: confirm hold
    O->>S: generate PDF labels
    O->>R: assign courier (SKIP LOCKED)
    O->>T: open journey
    O->>S: status dispatched (system)
    S--)K: status_changed → inventory commits the hold
    O->>O: start child DeliveryWorkflow(delivery-{id})
    Note over T: courier posts picked_up … delivered
    T--)K: tracking.milestone_recorded
    K--)O: signal field_update
    O->>S: picked_up · in_transit · out_for_delivery · delivered (system)
    S--)K: status_changed → courier completes the assignment
```

Details, failure handling and compensation: [§12](#12-dispatch-and-delivery).

---

## 9. Consistency and Idempotency

| Concern | Mechanism | ADR |
|---|---|---|
| Change + event atomicity | Transactional outbox, relay with `FOR UPDATE SKIP LOCKED` | [005](adr/ADR-005-transactional-outbox-kafka.md) |
| Duplicate events | `processed_events` per consumer, in the effect's transaction | [005](adr/ADR-005-transactional-outbox-kafka.md) |
| Client retries | `Idempotency-Key` per (caller, key), 60 s lease, 24 h replay, recorded atomically | [007](adr/ADR-007-layered-idempotency.md) |
| Double shipment on retry | Keyed shipment id = `uuid5(owner:key)` | [007](adr/ADR-007-layered-idempotency.md) |
| Concurrent shipment updates | Optimistic `version_id_col` → `409 concurrent_update` | [008](adr/ADR-008-concurrency-control.md) |
| Last-unit stock races | Row locks in id order; check constraints forbid negative or over-reserved stock | [008](adr/ADR-008-concurrency-control.md) |
| Leaked holds | Unconfirmed holds expire after 30 min; confirmed holds never | [009](adr/ADR-009-inventory-hold-lifecycle.md) |
| Stale replicas | Newest-wins on `source_updated_at` | [006](adr/ADR-006-event-carried-state-replicas.md) |
| One request, one commit | Services flush; the endpoint commits once | [008](adr/ADR-008-concurrency-control.md) |
| Double dispatch | Status gate (`ready_for_dispatch` only) + workflow id per attempt with `REJECT_DUPLICATE` | [010](adr/ADR-010-temporal-orchestration.md) |
| Half-done dispatch | Saga compensation in reverse; `dispatch_failed` with the reason; hold kept | [010](adr/ADR-010-temporal-orchestration.md) |
| Two dispatches, one courier slot | `FOR UPDATE SKIP LOCKED`; capacity check constraint; one active assignment per shipment (partial unique index) | [018](adr/ADR-018-courier-assignment.md) |
| Retried or out-of-order field updates | Device `event_id` as Mongo `_id`; journey moves only forward in time; workflow ignores duplicates and stale reports | [011](adr/ADR-011-mongodb-tracking.md) |

Two pods handling the same request at the same moment: the first claims the idempotency key,
the second gets `409 request_in_progress`; without a key, the database locks and constraints
decide, and Kafka delivery is deduplicated per consumer.

---

## 10. API and Security

- **One entry point**: Traefik on `:8000` routes by path prefix to the owning service;
  **one Swagger UI** for every service at `/docs`
  ([ADR-012](adr/ADR-012-edge-gateway-unified-docs.md)).
- **Tokens**: RS256 signed by identity, verified locally everywhere; refresh tokens stored
  hashed and rotated, replay revokes the user's sessions; lockout after 5 failed logins for
  15 minutes ([ADR-003](adr/ADR-003-token-verification-jwks.md)).
- **Authorisation in two layers**, enforced by each service: the **role** opens the route; the
  **scope** in the token (`warehouse_ids`, `courier_id`) narrows the rows. An operator sees
  only their warehouses' stock and shipments; a courier only their own shipments.
- **Errors** use one body in every service, `{code, message}` (plus `detail` with field
  errors on `422`), with a stable `code`
  (`insufficient_stock`, `concurrent_update`, `request_in_progress`,
  `upstream_unavailable`, …); database constraint violations map to specific messages through
  fixed constraint names.

| Role | Can do (summary) |
|---|---|
| admin | Everything, including user management |
| customer support | Create, update and cancel shipments (and the stock holds behind them); initiate returns; read stock and warehouses |
| warehouse operator | Within assigned warehouses: stock levels and adjustments, packages, `ready_for_dispatch`, `returned` |
| courier | Their own profile and availability, their assignments and journeys; post tracking updates; warehouse locations |
| `system` (service accounts) | Dispatch and delivery statuses, courier assignment, journeys — never given to a person |

Service accounts ([ADR-017](adr/ADR-017-service-accounts.md)): identity issues 5-minute
`system` tokens for client credentials configured in `SERVICE_CLIENTS`; only the orchestrator
has one today. The public tracking lookup (`GET /tracking/public/{reference_no}`) is the only
unauthenticated business route and returns the milestone timeline only.

---

## 11. Observability

| Signal | Now | Week 3 |
|---|---|---|
| Logs | Structured (JSON in Docker), one line per request with `request_id` (`X-Request-ID` honoured and returned) | `trace_id` / `span_id` on every line |
| Traces | OpenTelemetry for FastAPI, httpx and SQLAlchemy, exported to **Jaeger** (:16686) when an OTLP endpoint is set; `traceparent` forwarded on service calls | Context carried through Kafka headers and Temporal activities |
| Health | `/health` (liveness, touches nothing) and `/health/ready` (checks the service's own store: PostgreSQL, MongoDB, or Temporal for the orchestrator) on every service | — |
| Workflows | **Temporal UI** (:8233): every dispatch and delivery, each step's input, output, retries and failure; `GET /api/v1/dispatches/{workflow_id}` for the current step | Workflow outcome metrics |
| Metrics | — | Prometheus: RED per route, outbox backlog age, consumer lag, workflow outcomes, the nine business metrics |
| Dashboards and alerts | — | Grafana: API health, pipeline health, business KPIs |

Diagnosis targets from the brief: dispatch workflows (Temporal UI), courier
assignment and tracking updates (traces + workflow history), warehouse reservation (ledger +
traces), background workers (outbox backlog and consumer lag metrics).

---

## 12. Dispatch and Delivery

Decisions: **Temporal** orchestration ([ADR-010](adr/ADR-010-temporal-orchestration.md)),
**MongoDB** for tracking ([ADR-011](adr/ADR-011-mongodb-tracking.md)), **real PDF labels**,
**automatic courier assignment with manual override**
([ADR-018](adr/ADR-018-courier-assignment.md)), **service accounts** for background calls
([ADR-017](adr/ADR-017-service-accounts.md)).

### 12.1 Dispatch workflow

`POST /api/v1/shipments/{id}/dispatch` (admin, warehouse operator in scope) accepts a
`ready_for_dispatch` shipment with at least one package: it moves it to `dispatching`,
increments `dispatch_attempts`, stores `dispatch_workflow_id = dispatch-{id}-{attempt}` and
stages `shipment.dispatch_requested`, all in one transaction, and answers `202`. A second click
gets `409` (the status is no longer `ready_for_dispatch`). The orchestrator's trigger consumes
the event and starts the workflow with `REJECT_DUPLICATE`, so a redelivered event is a no-op.

| Step | Activity (service) | Business "no" (not retried) | Compensation |
|---|---|---|---|
| 1 | Validate: shipment is `dispatching`, this is its current attempt, has packages (shipment) | `stale_dispatch` (skip, touch nothing), `invalid_shipment` | — |
| 2 | Confirm the stock hold exists and is confirmed (inventory) | no hold | — (the hold is kept for a retry) |
| 3 | Generate one A6 PDF label per package with a Code128 barcode (shipment) | — | — (regenerated idempotently) |
| 4 | Assign a courier (courier); when nobody is free, wait durably and look again (`COURIER_WAIT_ATTEMPTS` × `COURIER_WAIT_SECONDS`) | `no_courier_available` after the last try | Release the assignment |
| 5 | Open the tracking journey (tracking) | — | Cancel the journey |
| 6 | Mark `dispatched` with the courier (shipment, as `system`) → inventory commits the hold by event | — | — |
| 7 | Start the child `DeliveryWorkflow` (`delivery-{id}`, survives the parent) | — | — |

Transient failures (timeouts, 5xx) are retried with backoff (8 attempts per step,
compensations up to 50). A business "no" runs the compensations in reverse and marks the
shipment `dispatch_failed` with the reason (e.g. `no_courier_available: …`). From there it can
be cancelled (the hold is released) or set `ready_for_dispatch` and dispatched again — a new
attempt with a new workflow id; the journey is reopened for the new courier.
`GET /api/v1/dispatches/{workflow_id}` shows the current step and completed steps.

### 12.2 Courier service

PostgreSQL. A courier has a code (`CR-0001`), an optional login (`user_id`; identity mirrors
`users.courier_id` from `courier.*` events), vehicle type, `max_active_shipments`, an optional
weight limit, home city and home warehouse, a status (`active`, `suspended`) set by admins and
an availability (`offline`, `available`, `on_break`) set by the courier
(`PUT /couriers/me/availability`) or by staff on their behalf.

**Assignment** (`POST /couriers/assignments`, `system` or admin): an active, available courier
in the warehouse's city with free capacity and a sufficient weight limit; home warehouse
first, then lowest load ratio, then longest since the last assignment. Candidates are locked
with `FOR UPDATE SKIP LOCKED`, so concurrent dispatches take different couriers. Assigning a
shipment that already has an active assignment returns it (safe for activity retries).
**Manual override**: `POST /couriers/assignments/{shipment_id}/reassign` (admin, customer
support, `system`) to a named courier or the next best; `/release` ends it. Couriers see their
own jobs at `GET /couriers/assignments/mine`.

### 12.3 Delivery workflow and tracking

- Couriers post updates to **tracking**: `POST /tracking/updates` (one) or
  `/tracking/updates/batch` (up to a device's offline backlog). Types are `milestone`
  (`picked_up`, `in_transit`, `out_for_delivery`, `delivery_failed` with a reason,
  `delivered`) and `location`. Each carries a client `event_id` (the event's Mongo `_id`), so a
  retry is reported as `duplicate`; a courier may only report on their own journeys; clocks
  more than 5 minutes ahead are rejected.
- The journey document moves only forward in `occurred_at` (a late update joins the timeline
  without regressing it). Each event is its own outbox entry, published by the tracking
  worker as `tracking.milestone_recorded`.
- The orchestrator's trigger forwards courier milestones as `field_update` **signals** to
  `DeliveryWorkflow`, the **only writer** of post-dispatch status. It ignores duplicates and
  stale reports and fills gaps: `out_for_delivery` straight after `dispatched` applies
  `picked_up` and `in_transit` first, so the state machine is always walked legally.
- **Timers**: no pickup within `PICKUP_TIMEOUT_SECONDS` (4 h) → reassign to another courier
  once; the `MAX_DELIVERY_ATTEMPTS`th (3rd) failed attempt → `return_initiated`.
- Assignments end by event; returns started by support and received at the warehouse appear on
  the journey timeline too. `GET /tracking/public/{reference_no}` serves customers.

### 12.4 Infrastructure

Temporal 1.29 (auto-setup) with its own PostgreSQL and the Temporal UI on :8233, MongoDB 7,
courier-db, and the courier, orchestrator and tracking APIs and workers — in Compose, the
gateway routes and `./dev.sh`.

---

## 13. Part B — GenAI Layer

The AI layer is a separate service with its own scaling profile (LLM latency, long-lived
streaming connections); a hung provider call must never affect dispatch traffic. It
integrates through Kafka and read-only queries.

- **Ingestion (Week 4):** an indexing consumer composes narrative documents from operational
  events (a shipment's lifecycle, exceptions and timings; periodic warehouse and courier
  summaries), embeds them and stores them with filterable metadata (`shipment_id`,
  `warehouse_id`, `courier_id`, `city`, `status`, `date`). Re-indexing is idempotent on
  `{entity_type, entity_id, version}`.
- **Assistant (Week 5):** a LangGraph agent with `semantic_search` (filtered retrieval),
  `metrics_query` (allow-listed read-only aggregates) and `shipment_lookup`, streamed over SSE.
  Requests carry the caller's token, so retrieval is scoped by the same rules as the REST API.
- **AI observability:** every interaction logged with retrieved context, latency, token usage
  and outcome; provider calls behind a timeout and circuit breaker.

---

## 14. Runtime Topology

| Component | Local port | Notes |
|---|---|---|
| Gateway (Traefik) | 8000 (dashboard 8080) | Docker labels in full-Docker mode; `infra/gateway/local.yml` when services run on the host |
| Swagger UI | 8000/docs | One page, a dropdown per service |
| identity · warehouse · inventory · shipment · courier · orchestrator · tracking APIs | 8001 – 8007 | Each also has `/docs` |
| Workers | — | One per service: outbox relay + consumers; the orchestrator's runs the Temporal worker and the Kafka trigger |
| PostgreSQL ×5 | 5441–5445 | One container and volume per service |
| Temporal · Temporal UI | 7233 · 8233 | Its own PostgreSQL (`temporal-db`) |
| MongoDB | 27017 | Tracking |
| Kafka (KRaft) | 29092 (host) / 9092 (containers) | Kafka UI on 8090 with `--profile tools` |
| Jaeger | 16686 | OTLP on 4318 inside the network |

Two run modes ([ADR-016](adr/ADR-016-cross-platform-developer-cli.md)):

- **Services on the host** (`./dev.sh infra up` + `./dev.sh start --reload`): databases,
  Kafka, Temporal, MongoDB, Jaeger, gateway and Swagger UI in Docker; APIs and workers as local processes with
  hot reload.
- **Everything in Docker** (`./dev.sh docker up`): one image built per service from
  `infra/docker/service.Dockerfile`; APIs migrate on start.

Production shape (not part of the assignment): each API and worker as its own Deployment,
scaled independently; Kafka, Temporal and PostgreSQL replicated.

---

## 15. Repository Layout

```
libs/sl-platform/            shared plumbing (no business rules)
services/<name>/
  src/<name>/                config, db, models, schemas, repository, services/, api/,
                             events.py, main.py (API), worker.py, seed.py
  migrations/                the service's own Alembic chain (SQL services)
  tests/
services/orchestrator/       workflows/, activities.py, trigger.py — no database
tests/e2e/                   every service in one process, an in-memory bus, a real Temporal dev server
infra/docker/                one Dockerfile for every service + entrypoint
infra/gateway/               gateway routes for local mode
infra/temporal/              Temporal dynamic config
scripts/                     dev CLI, API-collection and database-reference generators
docs/                        this document, PRD, database reference, ADRs, API collection
docker-compose.yml · dev.sh · dev.ps1 · dev.cmd · Makefile
```

---

## 16. Architecture Decision Records

| ADR | Decision | Status |
|---|---|---|
| [001](adr/ADR-001-microservices-database-per-service.md) | Microservices with a database per service | Accepted (supersedes the monolith) |
| [002](adr/ADR-002-service-boundaries.md) | Service boundaries | Accepted |
| [003](adr/ADR-003-token-verification-jwks.md) | RS256 tokens verified locally with JWKS | Accepted |
| [004](adr/ADR-004-synchronous-calls-reserve-then-record.md) | REST only where needed; reserve, then record | Accepted |
| [005](adr/ADR-005-transactional-outbox-kafka.md) | Transactional outbox to Kafka, consumer dedupe | Accepted |
| [006](adr/ADR-006-event-carried-state-replicas.md) | Event-carried state transfer, local replicas | Accepted |
| [007](adr/ADR-007-layered-idempotency.md) | Layered idempotency | Accepted |
| [008](adr/ADR-008-concurrency-control.md) | Concurrency control | Accepted |
| [009](adr/ADR-009-inventory-hold-lifecycle.md) | Inventory hold lifecycle | Accepted |
| [010](adr/ADR-010-temporal-orchestration.md) | Temporal for dispatch and delivery | Accepted, implemented |
| [011](adr/ADR-011-mongodb-tracking.md) | MongoDB for tracking journeys | Accepted, implemented |
| [012](adr/ADR-012-edge-gateway-unified-docs.md) | One gateway, one API portal | Accepted |
| [013](adr/ADR-013-monorepo-shared-platform.md) | Monorepo, shared platform library, enforced boundaries | Accepted |
| [014](adr/ADR-014-soft-deletion-audit-trail.md) | Soft deletion and immutable audit trail | Accepted |
| [015](adr/ADR-015-event-schemas-avro-deferred.md) | JSON envelopes now, Avro + Schema Registry Week 3 | Accepted |
| [016](adr/ADR-016-cross-platform-developer-cli.md) | One cross-platform developer CLI | Accepted |
| [017](adr/ADR-017-service-accounts.md) | Service accounts for service-to-service calls | Accepted |
| [018](adr/ADR-018-courier-assignment.md) | Courier assignment: automatic with manual override | Accepted |

---

## 17. Non-Functional Targets

| Attribute | Target | Verification |
|---|---|---|
| Throughput | 10,000 shipments/day sustained; 3× seasonal peak | Load test on create, dispatch and tracking paths (Week 3) |
| API latency | p95 < 150 ms reads, < 300 ms writes (excluding workflow completion) | Prometheus histograms (Week 3) |
| Tracking ingest | 500 updates/s sustained | Load test (Week 3) |
| Dispatch completion | p95 < 30 s end to end (when a courier is free) | Temporal metrics (Week 3) |
| Event lag | p95 < 5 s from commit to consumer effect | Consumer-lag metric (Week 3) |
| Fault isolation | One service or database down affects only the flows that need it | Stop each container in turn |
| Durability | No committed change or event lost on process crash | Outbox + Temporal; crash tests |
| Recoverability | Failed workflows retryable; failed events replayable | Temporal UI; dead-letter replay (Week 3) |
| Security | No secrets in code; mutations audited; scoping enforced in every service | Review + tests |

---

## 18. Assumptions, Open Questions and Risks

**Assumptions:** single region; single Kafka broker and Temporal cluster are acceptable for
the assignment (production needs replication); notification providers and route preparation
are mocks behind stable interfaces; currency, tax and billing are out of scope.

| Open question | Default if unanswered |
|---|---|
| Qdrant or pgvector for Part B? | Qdrant |
| Which LLM provider? | Provider-agnostic adapter |
| Is a real notification channel expected? | Mock adapters, logged and inspectable |
| Kubernetes manifests per service? | Not in Weeks 1–3; the monolith's manifests are on `phase_1` |

| Risk | Mitigation |
|---|---|
| Local stack breadth (≈15 containers by Week 3) slows development | Run modes and Compose profiles; `./dev.sh start` runs only what is asked |
| Temporal workflows are code that must stay deterministic | Workflow tests on a Temporal dev server with stub activities; all I/O in activities; versioning before changing a running workflow |
| Eventual consistency surprises (replicas, hold confirmation) | Documented windows; dispatch re-validates; e2e tests cover event paths |
| Test depth below Phase 1 (≈700 tests then, 233 now) | Every feature lands with unit, workflow and e2e tests; coverage gate in Week 3 |
| Contract drift between services before the Schema Registry | Additive-only payload changes; consumer tests; Avro in Week 3 |

---

## 19. Delivery Roadmap

| Week | Delivered |
|---|---|
| **1** ✅ | Four services with isolated databases, platform library, outbox + Kafka, replicas, idempotency, state machine, gateway, unified Swagger, Compose, dev CLI, 115 tests |
| **2** ✅ | Service accounts; courier service; orchestrator with `DispatchWorkflow` (saga) and `DeliveryWorkflow`; tracking service on MongoDB; PDF labels; Temporal + MongoDB in Compose; 233 tests ([build log](phase_2.md)) |
| **3** | Schema Registry + Avro, dead-letter topic, Celery + RabbitMQ (hold sweeper, notifications), analytics service and the nine metrics, Prometheus + Grafana, trace context through Kafka, Redis token denylist |
| **4** | Indexing consumer, document composition, embeddings, vector store, filtered retrieval |
| **5** | LangGraph assistant, tools, SSE streaming, AI observability |

Feature-level traceability is in [PRD.md §6](PRD.md#6-traceability).
