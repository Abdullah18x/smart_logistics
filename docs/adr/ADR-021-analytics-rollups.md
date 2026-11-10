# ADR-021: Analytics — facts, incremental daily rollups, nightly rebuild

**Status:** Accepted · **Date:** 2026-11-10

## Context

FR-5 asks for: total shipments, shipments dispatched over time, delivered, failed deliveries,
average delivery time, courier utilisation, warehouse throughput, return rate, and failed
events / workflow issues. Querying the owning services' databases is ruled out (ADR-001), and
dashboards poll, so answers must be cheap.

## Decision

- An **analytics** service (:8008, PostgreSQL :5446) builds its own read model from
  `shipment.events` and `courier.events`:
  - `milestones` — the fact table: one row per event that matters (created, dispatched,
    delivered, delivery_failed, return_initiated, cancelled, dispatch_failed, assigned),
    keyed by event id, with time, warehouse and courier.
  - `shipments` — latest state per shipment, guarded by the shipment's own `version` so a
    late event never rolls the status back; key timestamps for durations.
  - `daily_rollups` — `(day, scope warehouse|courier, id, metric) → value`.
  - `couriers` — capacity and current load, for utilisation.
  - `pipeline_issues` — dead letters (read from every `dlq.*` topic by pattern) and failed
    dispatches, with the consumer or warehouse, reason and error.
- **Incremental + rebuild**: one function, `rollups.deltas()`, says which counters a
  milestone moves. The consumer applies it in the milestone's transaction (`value + delta`
  upsert), so dashboards move within a second. A **nightly Celery job** replays the
  milestones of the last two days through the same function and replaces those days'
  rollups atomically; `POST /analytics/recompute` does it for any period after a replay or
  a fix. The facts are the truth; the rollups are a cache of them.
- Definitions: days are UTC; delivery time is created → delivered; return rate is returns
  started per shipment dispatched; failed-delivery rate is failures over delivery attempts;
  utilisation is active assignments over capacity of active couriers, right now.
- **Access**: admins and support see everything; warehouse operators see their warehouses
  (and couriers based there) and not the pipeline-wide failed events. `/summary` is cached in
  Redis for 30 s.
- Grafana's business dashboard reads the rollups directly (read-only datasource).

## Consequences

- Answers are O(days × warehouses), independent of shipment volume.
- Counters can be briefly wrong after an incident until the next rebuild — by design, and
  fixable on demand.
- New metrics need a new counter in `deltas()` and a recompute of the history to backfill.
