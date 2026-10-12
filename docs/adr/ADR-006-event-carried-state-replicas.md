# ADR-006: Event-carried state transfer instead of cross-service reads

**Status:** Accepted · **Date:** 2026-10-06

## Context

Inventory and shipment both need warehouse facts (does it exist, is it operational, what is
its code) on hot paths. Calling the warehouse service each time couples their availability
to it and adds latency; joining across databases is impossible.

## Decision

- Warehouse publishes **full snapshots** on every change (`warehouse.created`, `.updated`,
  `.status_changed`, `.deleted`, `.restored`).
- Inventory and shipment each keep a local, read-only **`warehouse_refs`** table built from
  those events. Updates are applied only if newer (`source_updated_at`), so out-of-order
  delivery cannot regress a row.
- A new service builds its replica by replaying the topic from the start (consumer groups
  start at `earliest`).
- Display fields are **snapshotted** onto records at write time (`origin_warehouse_code`,
  `sku_code`, `sku_name`, unit weight), so reads never need another service.
- What used to be a cascade is an event: deleting a warehouse removes operator scopes in
  identity via `warehouse.deleted`.

## Consequences

- Shipment and inventory keep working when the warehouse service is down.
- Replicas lag by the event latency; a shipment against a warehouse closed a moment ago can
  slip through, which the business accepts (dispatch re-validates in Week 2).
- Snapshots are deliberately historical: renaming a SKU does not rewrite old shipments.
