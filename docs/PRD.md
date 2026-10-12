# SmartLogistics — Product Requirements

**Product:** SmartLogistics backend (Part A: logistics core) · **Client:** TransFleet
**Status:** Week 1 delivered, Week 2 in progress · **Last updated:** 2026-10-12

This document covers what the brief asks of a PRD: key use cases, functional and
non-functional requirements, the delivery timeline, and traceability from each requirement to
what delivers it. How it is built is in [ARCHITECTURE.md](ARCHITECTURE.md).

Status legend: ✅ built and covered by tests · 🟡 partly built · 🔴 scheduled.

---

## 1. Problem

TransFleet's growth exposed slow, partly manual dispatch; inconsistent inventory across
warehouses; inefficient courier assignment at peak; tracking events that are delayed,
duplicated or lost; and delays under high traffic. SmartLogistics replaces this with a backend
that keeps shipment, stock, courier and tracking data consistent and recoverable under load.

## 2. Users

| Role | Who | Needs |
|---|---|---|
| **Admin** | Operations leadership, platform owners | Manage users, warehouses and the catalog; override any step |
| **Customer support** | Support agents | Create and correct shipments, cancel, start returns, answer "where is my parcel?" |
| **Warehouse operator** | Staff at one or more facilities | Manage stock, pack parcels, release shipments for dispatch, receive returns — only for their warehouses |
| **Courier** | Delivery partners | Receive assignments, record pickup, progress and delivery attempts — only for their shipments |

## 3. Key use cases

| ID | Use case | Actor | Status |
|---|---|---|---|
| UC-01 | Sign in, stay signed in securely, sign out of all devices | All | ✅ |
| UC-02 | Create users with a role; scope operators to warehouses | Admin | ✅ |
| UC-03 | Register a warehouse and change its status (admin); maintain its zones and opening hours (admin, operator) | Admin, operator | ✅ |
| UC-04 | Maintain the SKU catalog (admin); open stock positions and receive, count and write off stock with a ledger (admin, operator) | Admin, operator | ✅ |
| UC-05 | Create a shipment that holds its stock atomically, safely retried | Support | ✅ |
| UC-06 | Correct a shipment before dispatch (support); add and update its parcels (operator) | Support, operator | ✅ |
| UC-07 | Cancel before dispatch and get the stock back | Support | ✅ |
| UC-08 | See a shipment's full history: who changed what, when, why | Support, admin | ✅ |
| UC-09 | Dispatch a shipment: validate, confirm stock, print labels, assign a courier, start tracking — all or nothing | Operator | 🔴 Week 2 |
| UC-10 | Manage courier profiles and availability; reassign a shipment | Admin, courier, support | 🔴 Week 2 |
| UC-11 | Record pickup, progress, delivery attempts and delivery from the field, over flaky networks | Courier | 🟡 transitions built; tracking service Week 2 |
| UC-12 | Return a parcel after failed attempts | Support, operator | 🟡 transitions built; automatic return Week 2 |
| UC-13 | Be notified of milestones | Customer, ops | 🔴 Week 3 |
| UC-14 | Monitor the nine operational metrics on dashboards | Admin | 🔴 Week 3 |
| UC-15 | Ask the assistant about delays, throughput and courier performance | Admin, support | 🔴 Weeks 4–5 |

## 4. Functional requirements

### FR-1 Shipment and user management

| ID | Requirement | Status |
|---|---|---|
| FR-1.1 | Create, update and read shipments with items, packages, delivery address and metadata (service level, priority, promised date, declared value, instructions) | ✅ |
| FR-1.2 | Register users with one of four roles; activate, suspend, deactivate, restore; change role | ✅ |
| FR-1.3 | Scope warehouse operators to warehouses; enforce the scope on every read and write in every service | ✅ |
| FR-1.4 | Courier profiles with vehicle, capacity, home city and operational availability | 🔴 Week 2 |
| FR-1.5 | Shipment history and audit trail: every transition with actor, role, reason and time | ✅ |
| FR-1.6 | Every update stays consistent across dependent services (stock, replicas, scopes) | ✅ |
| FR-1.7 | Warehouses with zones and weekly opening hours; catalog of SKUs; stock positions with a movement ledger | ✅ |

### FR-2 Dispatch workflow

| ID | Requirement | Status |
|---|---|---|
| FR-2.1 | Validate shipment details before dispatch | 🔴 Week 2 |
| FR-2.2 | Reserve or confirm inventory | ✅ held at creation, confirmed by event; dispatch-time check Week 2 |
| FR-2.3 | Generate shipping labels (PDF per package, with barcode) | 🔴 Week 2 |
| FR-2.4 | Assign a courier automatically, with manual reassignment | 🔴 Week 2 |
| FR-2.5 | Initialise the tracking journey | 🔴 Week 2 |
| FR-2.6 | Mark dispatched only after every step succeeds; commit stock on dispatch | 🟡 stock commit on dispatch built; workflow Week 2 |
| FR-2.7 | Partial failures never corrupt shipment state; a failed dispatch can be retried | 🟡 compensation on create built; saga Week 2 |

### FR-3 Delivery workflow

| ID | Requirement | Status |
|---|---|---|
| FR-3.1 | Record pickup, status updates, delivery attempts and final confirmation | 🟡 transitions and attempt count built; tracking service Week 2 |
| FR-3.2 | Accept high-volume field updates; retries and duplicates are harmless | 🔴 Week 2 |
| FR-3.3 | Recover from network failures; out-of-order updates handled | 🔴 Week 2 |
| FR-3.4 | Real-time status visibility | 🟡 status queryable now; live journey Week 2 |
| FR-3.5 | Notifications and analytics updated on milestones | 🔴 Week 3 |

### FR-4 Distributed and event-driven behaviour

| ID | Requirement | Status |
|---|---|---|
| FR-4.1 | Events for shipment creation, dispatch, courier assignment, tracking milestones, delivery, returns | 🟡 shipment, stock, user and warehouse events built; courier and tracking Week 2 |
| FR-4.2 | Background processing independent of user-facing flows | ✅ worker process per service |
| FR-4.3 | Traceable and recoverable; no double-processing | ✅ outbox + consumer dedupe; 🔴 dead-letter topic Week 3 |
| FR-4.4 | Handle workload spikes | 🟡 workers scale independently; load tests Week 3 |

### FR-5 Analytics — 🔴 Week 3

Total shipments · shipments dispatched over time · delivered · failed deliveries · average
delivery time · courier utilisation · warehouse throughput · return rate · failed events /
workflow issues.

### FR-6 Observability

| ID | Requirement | Status |
|---|---|---|
| FR-6.1 | Clear separation of responsibilities between services | ✅ |
| FR-6.2 | Logging for all critical flows | ✅ structured logs with request ids |
| FR-6.3 | Diagnose failures in dispatch, courier assignment, tracking, reservation, workers | 🟡 traces to Jaeger; Temporal UI Week 2; metrics and dashboards Week 3 |

## 5. Non-functional requirements

| ID | Requirement | Target |
|---|---|---|
| NFR-1 | Scale | Tens of thousands of shipments per day; 3× seasonal peaks |
| NFR-2 | Latency | p95 < 150 ms reads, < 300 ms writes |
| NFR-3 | Consistency | No oversold stock; no lost or double-applied events; no half-dispatched shipments |
| NFR-4 | Durability and recovery | No committed change lost on a crash; failed work retryable |
| NFR-5 | Fault isolation | One service or database down affects only the flows that need it |
| NFR-6 | Security | Least-privilege roles and scopes; hashed secrets; audited mutations |
| NFR-7 | Maintainability | Independent services with enforced boundaries; documented decisions; automated tests |
| NFR-8 | Operability | One command to run locally on any OS; health and readiness probes |

Measurement and verification for each are in
[ARCHITECTURE.md §17](ARCHITECTURE.md#17-non-functional-targets).

## 6. Traceability

| Requirement | Delivered by | Verified by | Week |
|---|---|---|---|
| FR-1.1 | shipment: `POST/GET/PATCH /shipments`, `/packages` | `services/shipment/tests/test_shipments.py` | 1 ✅ |
| FR-1.2 | identity: `/users`, `/users/{id}/role`, `/status`, `/restore` | `services/identity/tests/test_users.py` | 1 ✅ |
| FR-1.3 | identity scopes + `warehouse_ids` claim; filters in warehouse, inventory, shipment | `test_users.py`, `test_shipments.py`, e2e `test_operator_tokens_scope_rows_in_every_service` | 1 ✅ |
| FR-1.4 | courier service | — | 2 |
| FR-1.5 | `shipment_status_history`, `GET /shipments/{id}/history` | `test_shipments.py` | 1 ✅ |
| FR-1.6 | outbox, replicas, `warehouse.deleted` → scopes | e2e `test_replicas_were_built_from_warehouse_events`, `test_warehouse_status_and_deletion_propagate_as_events` | 1 ✅ |
| FR-1.7 | warehouse service; inventory `/skus`, `/inventory`, `/adjustments`, `/movements` | `test_warehouses.py`, `test_reservations.py` | 1 ✅ |
| FR-2.2 | inventory `/reservations`, confirm on `shipment.created` | `test_reservations.py`, e2e `test_create_dispatch_and_stock_follows` | 1 ✅ |
| FR-2.6 | commit on `shipment.status_changed` → dispatched | e2e `test_create_dispatch_and_stock_follows` | 1 🟡 |
| FR-2.7 | release on failure (`shipments/services`), `uuid5` keyed ids | e2e `test_idempotent_retry_across_services_holds_stock_once` | 1 🟡 |
| FR-2.1, 2.3–2.5 | orchestrator `DispatchWorkflow`, courier, tracking, labels | workflow tests (Temporal test environment) | 2 |
| FR-3.1–3.4 | tracking service, `DeliveryWorkflow` | workflow + e2e tests | 2 |
| FR-4.2–4.3 | `sl_platform.outbox`, `sl_platform.consumer`, per-service workers | `libs/sl-platform/tests`, `services/*/tests/test_worker.py`, e2e cancellation test | 1 ✅ |
| FR-5 | analytics service | — | 3 |
| FR-6.2–6.3 | `sl_platform.logging_setup`, `sl_platform.telemetry`, Jaeger | manual (Jaeger UI) | 1 🟡 |
| Idempotent retries | `sl_platform.idempotency` | `libs/sl-platform/tests`, `test_shipments.py`, e2e | 1 ✅ |

## 7. Milestones

| Week | Focus | Deliverables | Status |
|---|---|---|---|
| 1 | Foundation and core services | Working APIs, database schemas, Docker setup, clean service structure | ✅ ([phase_1.md](phase_1.md)) |
| 2 | Dispatch and delivery workflows | Stable dispatch workflow, courier orchestration, reliable tracking state machine | In progress |
| 3 | Events and observability | Kafka hardening (Schema Registry, DLQ), Celery workers, notifications, analytics, dashboards, tracing | Planned |
| 4 | Retrieval layer (Part B) | Indexed operational data, semantic retrieval | Planned |
| 5 | AI logistics layer (Part B) | Assistant, optimisation tools, streaming | Planned |

## 8. Out of scope

Frontends; real carrier, map or notification providers (mocked behind interfaces); payments,
tax and billing; multi-region deployment; production cloud infrastructure.
