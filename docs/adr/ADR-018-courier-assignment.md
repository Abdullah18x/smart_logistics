# ADR-018: Courier assignment — automatic with manual override

**Status:** Accepted · **Date:** 2026-10-12

## Context

Every dispatched shipment needs exactly one courier. Two dispatches running at once must not
both take the last slot of a courier, and operations need to move a shipment to another
courier when something goes wrong.

## Decision

- The **courier** service owns couriers (profile, vehicle, capacity, home city and
  warehouse, status, availability) and assignments, in its own PostgreSQL database.
- **Automatic assignment** (`POST /api/v1/couriers/assignments`, called by the dispatch
  workflow) picks an active, available courier in the warehouse's city with free capacity
  and a sufficient weight limit, ordered by: home warehouse first, then lowest load ratio,
  then longest since last assignment. Candidates are read with `FOR UPDATE SKIP LOCKED`, so
  concurrent dispatches pick different couriers instead of waiting on each other.
- **Exactly one active assignment per shipment** is a partial unique index; assigning again
  for the same shipment returns the existing assignment (idempotent activity retries).
- **Manual override**: admins and support reassign (`/{shipment_id}/reassign`) to a named
  courier or to the next best one; the delivery workflow uses the same call when a pickup is
  overdue. Couriers involved are locked in id order to avoid deadlocks.
- Assignments end on events: `delivered`/`returned` complete them, `cancelled` and
  `dispatch_failed` release them; `active_assignments` is kept in step in the same
  transaction.
- Couriers are linked to logins by `courier.*` events: identity mirrors `users.courier_id`.

## Consequences

- No double-booking without a global lock; capacity is enforced by a check constraint.
- When nobody is free the dispatch workflow waits and retries (configurable) before failing
  with `no_courier_available`; the shipment can be dispatched again later.
