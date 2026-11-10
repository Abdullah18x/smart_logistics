# Week 3 — Events, Background Work and Observability (microservices)

**Branch:** `phase_3` · **Period:** 2026-11-10
**Result:** nine services. Every event has a registered contract and a dead-letter path with
replay; customers, couriers and operations are notified on milestones; the nine analytics
metrics are served from the event stream; background jobs run on Celery; every hop is
measured, alerted on and traced end to end; sign-out takes effect immediately. 337 tests.

Decisions taken before the build: **JSON Schema** (not Avro) in the Schema Registry,
**Mailpit** for real SMTP with **mock SMS and push**, analytics as **incremental daily
rollups plus a nightly rebuild**, and three extras — **Redis** (token denylist, caching),
a **DLQ replay CLI**, a **coverage gate in CI**. Load tests moved to Week 4.

---

## 1. Scope (from the execution plan)

| Week 3 scope | Delivered |
|---|---|
| Kafka hardening | JSON Schema contracts for all 30 event types, checked on write and read, registered in Confluent Schema Registry (BACKWARD); dead-letter topic per consumer; `./dev.sh dlq list \| replay` ([ADR-015](adr/ADR-015-event-contracts-schema-registry.md)) |
| Celery workers | Celery on RabbitMQ: notification sends and sweep, inventory hold sweeper, analytics nightly rebuild ([ADR-019](adr/ADR-019-celery-background-jobs.md)) |
| Notifications | notification service: rules, templates, delivery log, SMTP + mock SMS/push, retries, suppressions, resend ([ADR-020](adr/ADR-020-notification-service.md)) |
| Analytics | analytics service: milestone facts, daily rollups, dead-letter watcher, the nine metrics ([ADR-021](adr/ADR-021-analytics-rollups.md)) |
| Dashboards | Prometheus (4 scrape jobs, 12 alert rules) and Grafana (3 dashboards as code) ([ADR-022](adr/ADR-022-observability-stack.md)) |
| Tracing | `traceparent` through the outbox and Kafka headers, Celery and Temporal interceptors; trace ids in logs |
| Extras | Redis token denylist and cache ([ADR-023](adr/ADR-023-token-revocation-redis.md)); CI workflow with a coverage gate |

| Deliverable | Evidence |
|---|---|
| Hardened event pipeline | `test_contracts_and_dlq.py` (registry, contract violations, retries → DLQ, registry outage, replay markers) |
| Notifications on milestones | 27 notification tests; e2e dispatch test checks the customer and courier messages |
| Nine metrics | 24 analytics tests; e2e checks the summary and per-courier numbers after a delivery |
| Observability | `test_observability.py` (platform, orchestrator); 37 PromQL expressions parse-checked; KPI SQL run against the schema |

## 2. What was built

| Area | Highlights |
|---|---|
| **platform** | `metrics` (RED by route template, outbox, consumers, lag, DLQ, Celery); `tasks` (Celery factory, `run_async`); `telemetry` (trace context through Kafka and Celery); `contracts`, `schema_registry`, `dlq` (+ CLI); consumer `receive()` → contract check → retries → dead letter, retry topic per consumer, `after_commit` hooks; `revocation` (denylist) and `cache`; `run_worker(extra=)` |
| **every service** | `contracts.py` with its event types; `Auth` checks the denylist; `/metrics`; workers serve metrics on `METRICS_PORT` |
| **identity** | Denylists a user whenever it revokes all their sessions, and the presenting token on a single sign-out; tokens carry `iat_ms` |
| **shipment · courier** | `shipment.created` carries the recipient; courier snapshots carry phone and email (optional, BACKWARD-compatible) |
| **inventory** | Celery worker with the expired-hold sweep every 5 minutes |
| **orchestrator** | Temporal `TracingInterceptor` and SDK metrics; the trigger dead-letters unreadable or contract-breaking messages |
| **tracking** | `traceparent` stored on events; relay publishes with the schema id |
| **notification** (new) | :8009 / :5447 — see [ARCHITECTURE §12.5](ARCHITECTURE.md#125-notifications) |
| **analytics** (new) | :8008 / :5446 — see [ARCHITECTURE §12.6](ARCHITECTURE.md#126-analytics) |
| **infra and tooling** | Compose: RabbitMQ, Redis, Schema Registry, Mailpit, Prometheus, Grafana, two databases, APIs, workers and three Celery workers; gateway routes and Swagger entries; `./dev.sh` starts Celery workers with beat, `dlq`, `test --cov`; generated API docs (95 endpoints) and database docs (7 databases); `.github/workflows/ci.yml` |

## 3. How the new parts behave

| Situation | Behaviour | Test |
|---|---|---|
| A payload breaks its contract on write | The change rolls back with the event; nothing is published | `test_staging_an_event_checks_its_contract_so_the_change_rolls_back` |
| A consumer receives a payload that breaks its registered schema | Never handled; moved to `dlq.<consumer>` as `contract_violation` | `test_a_payload_that_breaks_its_registered_schema_is_never_handled` |
| A handler keeps failing | 5 attempts with backoff, then dead-lettered with the error; the partition moves on | `test_a_handler_that_keeps_failing_is_retried_then_dead_lettered` |
| Schema Registry down | Consumption continues; counted as unvalidated | `test_a_registry_outage_never_blocks_consumption` |
| Replay | Only the failed consumer reads `retry.<consumer>`; its dedupe makes a second replay harmless; analytics marks the issue replayed | `test_each_consumer_also_reads_its_own_retry_topic`, `test_each_letter_is_recorded_once_and_a_replay_marks_it` |
| Same event delivered twice to notification | One message | `test_the_same_event_twice_sends_nothing_new` |
| Email provider times out | Retried at 5 s, 10 s… up to 5 attempts, then `failed` | `test_a_provider_error_backs_off_then_succeeds`, `test_it_gives_up_after_the_attempt_limit` |
| Opted-out address | `suppressed`, nothing sent | `test_a_suppressed_address_is_never_messaged` |
| Celery publish lost | The sweep re-queues overdue rows every minute | `test_the_sweep_requeues_only_what_is_overdue` |
| A late, older shipment event | Counted on its day; status not rolled back | `test_a_late_older_event_counts_but_does_not_roll_the_status_back` |
| Rollups wrong after an incident | Rebuilt from facts nightly or on demand | `TestRecompute` |
| Operator asks for another warehouse's metrics | `403`; their summary shows only their warehouses | `test_a_warehouse_operator_sees_only_their_warehouse` |
| User signs out everywhere / is suspended | Their access tokens are refused by every service on the next request | `test_signing_out_everywhere_ends_access_tokens_in_every_service`, `test_a_suspension_takes_effect_on_the_next_request` |
| Redis down | Tokens accepted on signature (or refused with `DENYLIST_FAIL_CLOSED`); cache computes | `test_redis_down_fails_open_by_default_and_closed_when_asked`, `test_a_redis_outage_just_computes` |
| Abandoned stock hold | Released by the sweep | `test_the_sweep_releases_only_expired_unconfirmed_holds` |

## 4. Defects found during the build

| Found | Cause | Fix |
|---|---|---|
| A consumer would send notifications for work that rolled back | Enqueuing inside the handler's transaction | `after_commit` hooks run only after the commit |
| `dlq --help` failed in the dev CLI | argparse `REMAINDER` swallowed flags | `dlq` is passed straight through before argparse |
| A user-wide revocation would also block a login in the same second | `iat` has whole-second precision | Tokens carry `iat_ms`; the check compares milliseconds |
| Per-suite coverage failed the first suite under the gate | pytest-cov applied `fail_under` to each suite | Gate only on the combined report |
| Test fakes broke after `Publisher.send` gained `headers` | Fakes had the old signature | Updated in identity and tracking tests |
| A dispatch step hitting a retryable error crashed the orchestrator worker with `RecursionError` instead of retrying (Week 2 bug, first seen in a Week 3 manual run) | The activity raised the error `from` itself; Temporal's failure converter follows causes | `raise_for(error)` never chains an error to itself; regression test converts both kinds of failure |
| Dispatch retried `start_tracking` eight times against a `500` | After a SQL-only reset, a new shipment reused a reference number still held by an old journey in MongoDB; the duplicate-key handler assumed a concurrent open and dereferenced `None` | `409 reference_in_use` naming the other shipment (dispatch fails cleanly); `./dev.sh reset` also clears tracking's MongoDB |
| Three Celery workers on one machine: two refused by RabbitMQ (`RESOURCE_LOCKED`) | Same default node name `celery@<host>` | `-n <service>@%h` locally and in Compose |
| OTLP metrics `404` from Jaeger; possible duplicate request spans | FastAPI 0.142 auto-configures its own OpenTelemetry from `OTEL_EXPORTER_OTLP_ENDPOINT` | Native telemetry switched off; ours stays |

## 5. Verification

```
./dev.sh test     platform 57 · identity 45 · warehouse 17 · inventory 21 · shipment 40
                  courier 29 · orchestrator 45 · tracking 20 · notification 27
                  analytics 24 · e2e 12                                          = 337
./dev.sh check    ruff, format, import-linter (2 contracts kept), alembic check ×7
./dev.sh test --cov   combined line + branch coverage, gate 75 %
```

Built in a sandbox without Docker Hub, Temporal or MongoDB: there, the platform and seven
SQL services' suites passed (260 tests), lint and boundaries were clean, migrations went
up, down and up again, the e2e suite collected, PromQL and Grafana SQL were checked, and
coverage without the platform, tracking, orchestrator and e2e suites measured 78.8 %. The
tracking, orchestrator and e2e suites, the full coverage figure and the trace in Jaeger are
confirmed with `./dev.sh test --cov` and [manual-test-week3.md](manual-test-week3.md) on a
machine with the full stack, and in CI.

## 6. Deferred

| Item | Where it goes |
|---|---|
| Load tests (10k shipments/day, 500 tracking updates/s) | Week 4, against the dashboards built here |
| Per-customer notification preferences (beyond suppressions) | When a customer-facing app exists |
| Alertmanager routing (email/Slack) | Deployment hardening |
| Separate beat process when Celery workers scale out | With the first multi-replica deployment |
| Raise the coverage floor | After the first full CI run reports the real figure |
| Workflow versioning policy | With the first change to a running workflow |
| Secrets manager for service-account secrets and SMTP credentials | Week 4 hardening |
