# Week 2 — Dispatch & Delivery Workflows (microservices)

**Branch:** `microservices` · **Period:** 2026-10-12 → 2026-10-13
**Result:** seven independently deployable services. Dispatch is a Temporal saga across five
of them, couriers are assigned automatically with a manual override, and delivery is driven
by courier updates recorded in MongoDB. 233 tests pass.

Decisions taken before the build: **MongoDB** for tracking, **real PDF labels**, **automatic
courier assignment with manual override**, and closing the Week 1 review gaps first.

---

## 1. Scope (from the execution plan)

| Week 2 scope | Delivered |
|---|---|
| Dispatch workflow on Temporal | `DispatchWorkflow`: validate → confirm stock → labels → assign courier → open journey → mark dispatched, with compensation ([ADR-010](adr/ADR-010-temporal-orchestration.md)) |
| Courier assignment flow | courier service: profiles, availability, automatic assignment with `SKIP LOCKED`, manual reassign and release ([ADR-018](adr/ADR-018-courier-assignment.md)) |
| Tracking lifecycle | tracking service on MongoDB: journeys, single and batch updates, public lookup; `DeliveryWorkflow` applies them ([ADR-011](adr/ADR-011-mongodb-tracking.md)) |
| Duplicate prevention | Dispatch gated by status and per-attempt workflow ids; device `event_id` as Mongo `_id`; one active assignment per shipment; workflow ignores repeated signals |
| State transitions and validations | Post-dispatch statuses reserved to `system`; the delivery workflow fills gaps and drops stale reports; automatic return after the last failed attempt |

| Deliverable | Evidence |
|---|---|
| Stable dispatch workflow | 10 workflow tests (happy path, durable courier wait, each compensation, stale and duplicate attempts) + 3 e2e |
| Courier orchestration | 29 courier tests including concurrent dispatches against one slot |
| Reliable tracking state machine | 19 tracking tests + 7 delivery workflow tests + e2e from courier post to `delivered` |

## 2. What was built

| Area | Highlights |
|---|---|
| **platform** | `system` role; `ServiceTokenProvider` (cached, renewed before expiry); `ServiceClient` uses service tokens and renews once on a `401`; `StatelessProcessor` for consumers without SQL; `courier.events` and `tracking.events` topics; optional database in the app factory with custom readiness checks |
| **identity** | Client-credentials endpoint `POST /auth/service-token` (5-minute `system` tokens, constant-time secret check, stable subject per client) ([ADR-017](adr/ADR-017-service-accounts.md)); `system` can never be given to a user; `users.courier_id` mirrored from `courier.*` events |
| **courier** (new) | Couriers with vehicle, capacity, weight limit, home city and warehouse, status and self-set availability; assignments with a partial unique index; ranking by home warehouse, load ratio, idle time; reassignment by support or by the workflow; assignments completed or released by shipment events; seed fleet CR-0001…CR-0006 |
| **shipment** | `POST /shipments/{id}/dispatch` (202) with `dispatch_attempts` and the `dispatch_requested` event; A6 PDF labels with Code128 barcode stored per package (`POST /{id}/labels`, `GET /packages/{id}/label`); `courier_id` from `courier.assigned`; role table tightened |
| **orchestrator** (new, no DB) | `DispatchWorkflow` (saga), `DeliveryWorkflow` (signals, timers), activities over HTTP as the service account, Kafka trigger that starts workflows and forwards courier milestones, `GET /dispatches/{workflow_id}` |
| **tracking** (new, MongoDB) | `journeys` and `tracking_events`; dedupe by `event_id`; forward-only milestone updates; courier ownership and clock-skew checks; batches processed item by item; each event its own outbox entry with a leased relay; public lookup by reference |
| **infra and tooling** | Compose: Temporal + its PostgreSQL + UI (:8233), MongoDB 7, courier-db (:5445), three APIs and workers; gateway routes; `./dev.sh` knows all seven services, warns when Temporal or MongoDB is down; API docs cover 82 endpoints; database docs include courier and the Mongo collections |

### Week 1 review gaps closed first

Docs rewritten to match the code, worker tests in every service, the shipment worker written,
Docker fixes (Traefik v3.6, per-service image layout, migrations skipped for services without
Alembic), workers retrying while Kafka is down, wrappers always syncing the virtualenv.

## 3. How dispatch and delivery behave

| Situation | Behaviour | Test |
|---|---|---|
| Normal dispatch | Every step in order; shipment `dispatched` with its courier; labels printed; stock committed; journey open | e2e `test_dispatch_runs_every_step_and_delivery_follows_the_courier` |
| Double click | Second `POST /dispatch` → `409`; one workflow, one assignment | e2e `test_a_double_click_dispatches_once` |
| Event redelivered | Same workflow id → rejected by Temporal, nothing runs twice | `test_trigger.py`, `test_the_same_attempt_can_never_start_twice` |
| No courier free | Waits and retries durably; then releases nothing it did not take, marks `dispatch_failed: no_courier_available`, keeps the hold | e2e `test_no_courier_fails_the_dispatch_cleanly_and_a_retry_succeeds` |
| Retry after failure | `ready_for_dispatch` → dispatch again: attempt 2, new workflow, journey reopened | same e2e, `test_a_new_attempt_after_failure_does_start` |
| Failure after courier booked | Journey cancelled, courier released (reverse order), `dispatch_failed` | `test_a_late_failure_undoes_everything_in_reverse_order` |
| Service briefly down | Activity retried with backoff; the workflow does not fail | `test_transient_errors_are_retried_not_failed` |
| Two dispatches, last courier slot | One gets the courier, the other the next candidate or `no_courier_available` | `test_concurrent_dispatches_never_overbook_the_last_slot` |
| Device offline, sends a batch | Each item recorded once; workflow walks `picked_up → in_transit → out_for_delivery → delivered` | e2e, `test_gaps_from_an_offline_device_are_filled_in_order` |
| Same update sent twice / late update | `duplicate` result; journey never moves back; workflow ignores it | `test_a_milestone_is_recorded_once_however_often_it_is_sent`, `test_duplicates_and_stale_reports_change_nothing` |
| Third failed attempt | `return_initiated` automatically | `test_the_last_failed_attempt_starts_a_return` |
| Nobody picks it up | Reassigned to another courier once | `test_an_overdue_pickup_is_reassigned_once` |
| Someone else's shipment | A courier cannot report on it; staff cannot set field statuses by hand | `test_only_the_assigned_courier_reports`, `test_field_statuses_are_written_only_by_the_workflow` |

## 4. Defects found during the build

| Found | Cause | Fix |
|---|---|---|
| A pickup timeout ended the delivery workflow | `workflow.wait_condition(timeout=…)` raises `TimeoutError` | Caught; it triggers the reassignment and the wait starts again |
| Journeys stayed at `dispatched` when a device clock ran slightly behind | The forward-only rule compared device time with the server time of `dispatched` | The first field milestone always moves the journey off `dispatched` |
| Workflow results came back as dicts in callers | Untyped workflow handles | Typed result where the caller needs fields; tests read either |
| Starting a duplicate workflow raised an unexpected type | Wrong import path for `WorkflowAlreadyStartedError` | Imported from `temporalio.exceptions` |
| Existing shipment tests broke once dispatch was workflow-only | They set `dispatched` by hand as admin | Tests dispatch through a helper using a `system` token, as production does |

## 5. Verification

```
./dev.sh test     platform 17 · identity 41 · warehouse 17 · inventory 19 · shipment 40
                  courier 29 · orchestrator 39 · tracking 19 · e2e 12              = 233
./dev.sh check    ruff, format, import-linter (2 contracts kept), alembic check ×5
```

Workflow tests run on a local Temporal dev server with short timeouts and courier waits
(a sub-second pickup timeout stands in for the 4-hour default). The e2e suite loads every service in one process, each on its own store, joined
by an in-memory event bus, with a real Temporal dev server and the orchestrator's real
activities calling the services over HTTP with a service-account token. Tracking tests need a
MongoDB-compatible server on `MONGO_URL`; orchestrator and e2e tests need the Temporal CLI
(`TEMPORAL_CLI_PATH`, or the SDK downloads it). Migrations downgrade and upgrade cleanly.

## 6. Deferred

| Item | Where it goes |
|---|---|
| Notifications on milestones | Notification service (Celery + RabbitMQ), Week 3 |
| Analytics metrics (courier utilisation, delivery time, return rate…) | Analytics service, Week 3 |
| Temporal metrics, workflow dashboards, trace context into activities | Prometheus + Grafana, Week 3 |
| Load tests for tracking ingest (500 updates/s target) | Week 3 |
| Dead-letter topic, Schema Registry for the new event types | Week 3 |
| Workflow versioning policy before the first change to a running workflow | With the first such change |
| Secrets manager for service-account secrets | Week 4 hardening |
| Docker images for the three new services built here only by Compose config validation | Build and smoke-test with `./dev.sh docker up` on a machine with Docker Hub access |
