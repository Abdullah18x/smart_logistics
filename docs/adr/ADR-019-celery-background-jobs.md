# ADR-019: Background jobs on Celery with RabbitMQ

**Status:** Accepted · **Date:** 2026-11-10

## Context

Week 3 adds work that is neither a request nor a reaction to one event: sending a
notification (slow, retried on provider errors), rebuilding analytics counters every night,
and releasing inventory holds that were never confirmed. The brief names Celery for
background processing. Kafka consumers already exist for event handling; Temporal runs the
multi-step dispatch saga.

## Decision

- **Celery on RabbitMQ** for jobs: one Celery app per service (`<svc>.tasks:celery_app`)
  built by `sl_platform.tasks.create_celery`, consuming its own queue named after the service.
  RabbitMQ is the broker; there is **no result backend** — callers never wait on a task.
- **Safe defaults**: `acks_late`, `reject_on_worker_lost`, prefetch 1, JSON only, time limits.
  A task that crashes is redelivered, so every task is idempotent (it re-reads state from its
  database and acts only if still needed).
- **The database is the source of truth, not the queue.** A notification row is written by
  the consumer and the task only carries its id; the attempt count, backoff and final status
  live in the row. A beat sweep re-queues anything overdue, so a lost publish (broker down, a
  crash between commit and enqueue) only delays work.
- **One process per Celery worker** (`--pool=solo`): tasks share the process's event loop
  and database engine (`run_async`), and its metrics are served from that same process.
  Throughput scales with more worker containers, not with forked children.
- **Beat** runs inside the service's Celery worker (`-B`): notification `requeue-overdue`
  (60 s), inventory `release-expired-holds` (300 s), analytics `nightly-recompute` (02:17 UTC).
- **Which tool for what**: Kafka consumers react to facts; Celery runs jobs (retries,
  schedules); Temporal coordinates long-running multi-service processes with compensation.
- Task outcomes and durations are Prometheus metrics (signals in the platform), and traces
  continue from whoever queued the task (OpenTelemetry Celery instrumentation).

## Consequences

- One more broker to run (RabbitMQ in Compose, :15672 UI). Its loss pauses sending and
  sweeps but loses nothing: rows stay pending and the sweep re-queues them.
- Single beat per service is assumed (one Celery container); scaling workers out means
  running beat separately (`celery beat`) so schedules do not fire twice.
