# Manual test — Week 3 (events, notifications, analytics, observability)

Six short scenarios. Swagger is at **http://localhost:8000/docs**.

- **A** — the customer and courier are told (Mailpit, delivery log)
- **B** — the nine metrics (API and Grafana)
- **C** — one trace across services, dashboards and alerts
- **D** — a failed event lands in a dead-letter topic and is replayed
- **E** — sign-out takes effect immediately in every service
- **F** — the hold sweeper releases an abandoned hold

> Tick the boxes as you go. Request bodies are ready to paste into Swagger's "Try it out" box.

---

## Setup

```bash
uv sync --all-packages   # new: celery, redis, jinja2, aiosmtplib, fakeredis
./dev.sh infra up        # adds RabbitMQ, Redis, Schema Registry, Mailpit, Prometheus, Grafana
./dev.sh migrate         # analytics and notification are new
./dev.sh seed
./dev.sh start --reload  # 9 APIs + workers + 3 Celery workers (inventory, analytics, notification)
```

Wait for `==> running: ...`. The Celery workers show up in the log as `inventory-celery`,
`analytics-celery` and `notification-celery`.

| Open | Where | Log in |
|---|---|---|
| Swagger, all services | http://localhost:8000/docs | `admin@transfleet.com` / `SmartLogistics!2026` |
| Mailpit (emails) | http://localhost:8025 | — |
| Grafana | http://localhost:3000 | anonymous viewer (admin/admin to edit) |
| Prometheus | http://localhost:9090 | — |
| Jaeger | http://localhost:16686 | — |
| RabbitMQ | http://localhost:15672 | guest / guest |
| Schema Registry | http://localhost:8081/subjects | — |
| Temporal UI | http://localhost:8233 | — |

---

## A — notifications

### A1 · Create a shipment with an email address

- [ ] `POST /api/v1/shipments`

```json
{
  "customer_reference": "ORD-WEEK3-1",
  "origin_warehouse_id": "ffd130be-7beb-5f93-8f02-c18d1ff15fda",
  "service_level": "express",
  "destination_address": {
    "contact_name": "Ayesha Khan",
    "contact_phone": "+923001234567",
    "contact_email": "ayesha.khan@example.com",
    "line1": "House 12, Street 4, DHA Phase 6",
    "city": "Karachi",
    "country_code": "PK"
  },
  "items": [{ "sku_id": "6c5d7270-9a58-50d8-9717-3da9eaaa859c", "quantity": 1 }]
}
```

- [ ] Mailpit → **"We've got your order — SL-2026-…"** addressed to Ayesha Khan.

### A2 · Dispatch and deliver it

Same steps as Week 2 (A2–A5 in [manual-test-week2.md](manual-test-week2.md)): set
`ready_for_dispatch`, add a package, dispatch, then report `picked_up`, `out_for_delivery`,
`delivered` as the courier.

- [ ] Mailpit → **"Your shipment SL-2026-… is on its way"** with a tracking link, then
  **"Delivered — SL-2026-…"**
- [ ] The tracking link opens the public lookup (no token)
- [ ] Terminal, `notification-celery` lines for the SMS and push mocks:

```
SMS to +923001234567: Hi Ayesha Khan, ... has left the warehouse ... (mock-sms-3f9c1a2b7d10)
PUSH to e0271f09-...: New job: SL-2026-000007 from KHI-01. Open the app to start. (mock-push-...)
SMS to <the courier's phone>: New job: SL-2026-000007 from KHI-01. ...
```

### A3 · The delivery log

- [ ] `GET /api/v1/notifications?shipment_id={shipment_id}` (as admin or support)

**→** one row per message: `shipment_confirmed` email, `new_assignment` push + sms,
`shipment_dispatched` email + sms, `out_for_delivery` sms, `delivered` email + sms — all
`sent`, each with a `provider_message_id`.

- [ ] `POST /api/v1/notifications/{id}/resend` on the delivered email → `202`, a new row with
  `resend_of`, and a second email in Mailpit.
- [ ] As the courier: `GET /api/v1/notifications/mine` → only their own messages.

### A4 · Suppress an address

- [ ] `POST /api/v1/notifications/suppressions`

```json
{ "channel": "email", "address": "ayesha.khan@example.com", "reason": "asked to stop" }
```

- [ ] Create another shipment with the same email (A1) → no new email in Mailpit; the log
  shows the confirmation as `suppressed`.
- [ ] `DELETE /api/v1/notifications/suppressions/{id}` (admin) to undo.

> **Celery stopped?** Messages stay `pending` and are sent within a minute of it coming back
> (the `requeue-overdue` sweep). Try it: stop RabbitMQ with `docker compose stop rabbitmq`,
> create a shipment, start RabbitMQ again.

---

## B — the nine metrics

- [ ] `GET /api/v1/analytics/summary` (default: the last 30 days)

```json
{
  "period": { "from": "2026-10-12", "to": "2026-11-10" },
  "total_shipments": 2, "dispatched": 1, "delivered": 1,
  "failed_deliveries": 0, "failed_delivery_rate": 0.0,
  "average_delivery_hours": 0.12,
  "returns": 0, "return_rate": 0.0, "cancelled": 0,
  "courier_utilisation": { "active_couriers": 5, "busy_couriers": 0, "utilisation": 0.0, ... },
  "warehouse_throughput": [ { "warehouse_code": "KHI-01", "dispatched": 1, ... } ],
  "failed_events": { "dead_letters": 0, "dispatch_failures": 0 }
}
```

- [ ] `GET /api/v1/analytics/timeseries?metric=dispatched` → one point per day, zeros filled
- [ ] `GET /api/v1/analytics/couriers` → assigned / delivered per courier, current load
- [ ] Log in as `wh.lahore@transfleet.com`: `/summary` shows Lahore only, no `failed_events`
- [ ] Grafana → **SmartLogistics / Business KPIs**: the same numbers, last 30 days
- [ ] `POST /api/v1/analytics/recompute` with `{"from": "2026-11-01", "to": "2026-11-10"}`
  (admin) → `202`; `analytics-celery` logs `rebuilt rollups ... from N milestone(s)`, and the
  numbers do not change (the counters already matched the facts)

> `/summary` is cached for 30 s — a number you expect to move may take that long.

---

## C — one trace, dashboards, alerts

### C1 · A dispatch, end to end in Jaeger

- [ ] Jaeger → Service **shipment** → Operation `POST /api/v1/shipments/{shipment_id}/dispatch`
  → Find traces → open the one from A2.

You should see one trace containing: the HTTP request in shipment → `publish
shipment.events` (outbox relay) → `consume shipment.events` in the orchestrator → the
Temporal workflow and its activities (`StartActivity:...` / `RunActivity:...`) → their HTTP
calls into shipment, inventory, courier and tracking → the consumers in inventory, courier,
notification and analytics → `notification.send` in Celery.

- [ ] Any log line from those services carries the same `trace_id` as the trace.

### C2 · Dashboards

- [ ] Grafana → **API health**: requests/s per service, 5xx ratio, read and write p95 with the
  150 ms / 300 ms target lines, slowest routes.
- [ ] Grafana → **Event pipeline**: outbox age, consumer throughput by outcome,
  end-to-end lag p95 (target 5 s), consumer lag, dead letters, Celery tasks, notifications,
  Temporal workflows.
- [ ] Prometheus → Status → **Targets**: every API, worker and Celery worker `UP`
  (anything you did not `start` is `DOWN`).

### C3 · An alert fires

- [ ] Stop one service (Ctrl+C the whole `start`, then `./dev.sh start` without `courier`).
- [ ] Prometheus → **Alerts**: `TargetDown` for courier goes *pending*, then *firing* after
  a minute. Start it again and the alert clears.

---

## D — dead letters and replay

Make inventory's consumer fail on purpose: stop its database, then change a warehouse
(inventory keeps a copy of every warehouse, so it must apply the event).

- [ ] `docker compose stop inventory-db`
- [ ] `PATCH /api/v1/warehouses/ffd130be-7beb-5f93-8f02-c18d1ff15fda` → `{ "name": "Karachi Central" }`

Inventory's worker logs five failed attempts (2 s, 4 s, 8 s, 16 s apart), then:

```
inventory moved <event-id> (warehouse.updated) to dlq.inventory (handler_failed): ...
```

- [ ] `./dev.sh dlq list` → one letter: consumer `inventory`, reason `handler_failed`, the error
- [ ] `GET /api/v1/analytics/issues?kind=dead_letter` → the same letter, `status: open`
- [ ] Prometheus → Alerts: `DeadLetters` firing
- [ ] `docker compose start inventory-db`
- [ ] `./dev.sh dlq replay inventory --all` → `replayed warehouse.updated <event-id> ->
  retry.inventory`; inventory's worker processes it, and its replica now says *Karachi Central*:
  `docker compose exec inventory-db psql -U inventory -c "select code, name from warehouse_refs"`
- [ ] `./dev.sh dlq list` and `/analytics/issues` → `replayed`

Other consumers (shipment, courier) handled the event the first time and are not involved
in the replay.

---

## E — sign-out is immediate

- [ ] Log in as `support.lead@transfleet.com`; Authorize with that token.
- [ ] `GET /api/v1/shipments` → `200`
- [ ] `POST /api/v1/auth/logout` with `{}` (sign out of every session) → `200`
- [ ] `GET /api/v1/shipments` with the same token → **`401` "Token has been revoked."** — in
  shipment, not just identity. Before Week 3 this kept working until the token expired.
- [ ] Log in again → the new token works.

Same for an admin suspending someone: `PATCH /api/v1/users/{id}/status` with
`{"status": "suspended"}` and that user's next request to any service is `401`.

> Run with `SL_REDIS=off ./dev.sh start` and the old behaviour returns (tokens valid until
> expiry) — that is what happens if Redis goes down, unless `DENYLIST_FAIL_CLOSED=true`.

---

## F — the hold sweeper

Holds for shipments that never got recorded expire after 30 minutes; the sweep runs every
5 minutes. To see it quickly, start inventory with a 1-minute expiry:

```bash
UNCONFIRMED_HOLD_MINUTES=1 HOLD_SWEEP_SECONDS=30 ./dev.sh start --reload
```

- [ ] `POST /api/v1/inventory/reservations` (as support) for any SKU at KHI-01, with a
  random `shipment_id` that will never be created
- [ ] `GET /api/v1/inventory?...` → `reserved_qty` up
- [ ] Within ~90 s, `inventory-celery` logs `released 1 expired hold(s)` and `reserved_qty`
  is back.

---

## What changed in the logs

```
[notification-celery] Task notification.send[...] succeeded in 0.04s: 'sent'
[analytics-celery]    rebuilt rollups 2026-11-09..2026-11-10 from 12 milestone(s)
[inventory-celery]    released 1 expired hold(s)
[shipment]            ... trace_id=4bf92f3577b34da6a3ce929d0e0e4736 span_id=00f067aa0ba902b7
[inventory-worker]    inventory moved ... to dlq.inventory (handler_failed): ...
```

## If something does not show up

| Symptom | Check |
|---|---|
| No email in Mailpit | Is `notification-celery` running? Is RabbitMQ up? `GET /api/v1/notifications` shows `pending` rows waiting |
| Metrics stay at zero | Is `analytics-worker` running? `/summary` is cached 30 s |
| Grafana empty | Prometheus → Targets; the Business KPIs board reads analytics-db directly |
| No cross-service trace | `SL_TRACING` must not be `off`; Jaeger must be up before `start` |
| `dlq list` says Kafka unavailable | `./dev.sh infra up` |
| Schema errors on publish | `curl localhost:8081/subjects`; `SL_SCHEMA_REGISTRY=off ./dev.sh start` skips the registry |
