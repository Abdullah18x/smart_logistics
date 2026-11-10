# ADR-022: Observability — Prometheus, Grafana, OpenTelemetry end to end

**Status:** Accepted · **Date:** 2026-11-10

## Context

FR-6.3: diagnose failures in dispatch, courier assignment, tracking, reservation and workers.
Week 1 gave structured logs with request ids and HTTP traces to Jaeger; a request that crosses
Kafka, a Celery task or a Temporal activity lost its trace, and nothing was measured.

## Decision

- **Metrics** with `prometheus_client`, defined once in `sl_platform.metrics`:
  RED per route template (not raw path) on every API's `/metrics`; outbox backlog and oldest
  pending age; per-consumer outcomes, handler time, **end-to-end lag** (event committed →
  consumer effect committed, target p95 < 5 s), Kafka lag, dead letters, unvalidated events;
  Celery task outcomes and durations; Temporal SDK metrics from the orchestrator worker;
  service-specific counters (notifications, denylist checks, cache). Processes without HTTP
  serve `/metrics` on `METRICS_PORT`.
- **Prometheus** scrapes four jobs (api, worker, celery, temporal-sdk) with a generated
  config for both run modes (`infra/prometheus/{docker,local}.yml`) and evaluates 12 alert
  rules (`rules/alerts.yml`): targets down, 5xx rate, latency targets from NFR-2, outbox
  stuck, event lag, consumer lag, dead letters, registry outage, failing tasks,
  notifications and workflows. No Alertmanager locally.
- **Grafana** (:3000, anonymous viewer) is provisioned with Prometheus, Jaeger and the
  analytics database, and three dashboards generated from `infra/grafana/build_dashboards.py`:
  API health, event pipeline, business KPIs.
- **Tracing** with OpenTelemetry to Jaeger (OTLP :4318): the W3C `traceparent` is stored with
  each outbox row and sent as a Kafka header, consumers continue it; Celery tasks continue the
  trace of whoever queued them; Temporal's `TracingInterceptor` carries it into workflows and
  activities. Log lines carry `trace_id`/`span_id`.

## Consequences

- One trace shows HTTP → outbox → Kafka → consumer → Temporal activity → HTTP.
- Label cardinality is bounded (route templates, consumer names, partitions).
- Dashboards are code; edits made in the Grafana UI are not saved (provisioned read-only).
