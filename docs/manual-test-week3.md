# Test Week 3 yourself — from a clean start

Everything in order: start the stack, run the automated suites, then check each Week 3
feature by hand. About an hour end to end; each part stands on its own after Part 1.

| Part | What you check | Time |
|---|---|---|
| [1](#1--start-from-a-clean-slate) | Clean start: infrastructure, migrations, seed data, services | 10 min |
| [2](#2--automated-tests) | Lint, migrations, every test suite, coverage gate | 10 min |
| [3](#3--log-in) | Tokens for Swagger | 2 min |
| [4](#4--a-shipment-from-order-to-door-with-notifications) | A shipment from order to door, and every message it sends | 15 min |
| [5](#5--the-nine-metrics) | The nine analytics metrics, API and Grafana | 5 min |
| [6](#6--one-trace-dashboards-alerts) | One trace across services, dashboards, an alert firing | 10 min |
| [7](#7--a-failed-event-dead-letter-and-replay) | A failed event: dead letter, then replay | 5 min |
| [8](#8--sign-out-takes-effect-everywhere) | Sign-out takes effect in every service at once | 3 min |
| [9](#9--background-jobs-survive-an-outage) | Background jobs survive a RabbitMQ outage | 3 min |
| [10](#10--the-hold-sweeper) | The hold sweeper releases abandoned stock | 3 min |
| [11](#11--suppressions-and-resend) | Suppressing an address, resending a message | 3 min |

> Tick the boxes as you go. Request bodies are ready to paste into Swagger's
> "Try it out" box — **replace** the example Swagger fills in.

---

## 1 · Start from a clean slate

Week 3 adds two services that build their data from the event history, so start from
empty databases and an empty Kafka. Old Week 2 events (without contracts or recipients)
would otherwise be replayed into them.

### 1.1 Stop anything still running

- [ ] Ctrl+C any `./dev.sh start` terminal.
- [ ] Wipe the old containers and volumes (all local data goes):

```bash
./dev.sh docker down -v
```

### 1.2 Install and start the infrastructure

```bash
./dev.sh setup        # installs the new packages (celery, redis, jinja2, aiosmtplib, fakeredis)
./dev.sh infra up     # pulls the new images on first run — a few minutes
```

- [ ] `./dev.sh infra status` → every container `running` / `healthy`:
  7 Postgres (`identity-db` … `notification-db`), `kafka`, `schema-registry`, `temporal`,
  `temporal-ui`, `mongo`, `rabbitmq`, `redis`, `mailpit`, `prometheus`, `grafana`,
  `jaeger`, `docs`, `gateway-local`.

### 1.3 Create the schemas and the sample data

```bash
./dev.sh migrate      # 7 databases, including analytics and notification
./dev.sh seed         # warehouses, users, SKUs and stock, couriers
```

- [ ] Both end without errors. The seed's events wait in each service's outbox until the
  workers start.

### 1.4 Start every service

```bash
./dev.sh start --reload
```

- [ ] Wait for `==> running: identity :8001, … notification :8009 (+ workers)`.
  Every line is prefixed with the process: `shipment`, `shipment-worker`,
  `notification-celery`, …
- [ ] Three Celery workers start: `inventory-celery`, `analytics-celery`,
  `notification-celery`, each with `celery@… ready.`
- [ ] In a second terminal: `./dev.sh status` → all nine `ready`.

### 1.5 First sanity checks

| Open | Expect |
|---|---|
| http://localhost:8000/docs | Swagger, with **analytics** and **notification** in the top-right dropdown |
| http://localhost:8081/subjects | A JSON list like `["warehouse.events-warehouse.created", …]`: the contracts registered when the seed events were published |
| http://localhost:9090/targets | Jobs `api`, `worker`, `celery`, `temporal-sdk`, all `UP` |
| http://localhost:3000 | Grafana → Dashboards → **SmartLogistics**: three dashboards |
| http://localhost:8025 | Mailpit, empty inbox |
| http://localhost:15672 | RabbitMQ (guest / guest) → Queues: `inventory`, `analytics`, `notification` |

> Every URL in one place: Swagger :8000/docs · Temporal :8233 · Jaeger :16686 ·
> Grafana :3000 · Prometheus :9090 · Mailpit :8025 · RabbitMQ :15672 · Schema Registry :8081

---

## 2 · Automated tests

The tests use their own `*_test` databases, so they don't touch the data you just seeded.
They need the infrastructure from 1.2. The first run downloads the Temporal dev server.

```bash
./dev.sh lint          # ruff, format, service boundaries
./dev.sh check         # lint + "do the migrations match the models?" for all 7 databases
./dev.sh test --cov    # every suite + e2e, combined coverage, gate at 75 %
```

- [ ] `lint`: `Contracts: 2 kept, 0 broken.`
- [ ] `check`: `No new upgrade operations detected.` seven times
- [ ] `test --cov`: every suite passes. Expected counts:

```
sl-platform 57 · identity 45 · warehouse 17 · inventory 21 · shipment 40 · courier 29
orchestrator 45 · tracking 20 · analytics 24 · notification 27 · e2e 12      = 337
```

- [ ] The last block is the coverage table, ending `TOTAL … NN.N%`, with no
  `Coverage failure`. **Send me the `TOTAL` line**: I'll raise the gate to match it.

One suite at a time: `./dev.sh test analytics`. A failure there, without `--cov`, gives
the shortest output to share.

---

## 3 · Log in

- [ ] `POST /api/v1/auth/login` (Swagger → **identity**):

```json
{ "email": "admin@transfleet.com", "password": "SmartLogistics!2026" }
```

- [ ] Copy `access_token` → **Authorize** (top right) → paste → Authorize. The token is kept
  when you switch services in the dropdown.

Other accounts (same password) used below: `support.lead@`, `wh.lahore@`, `courier.one@`,
`courier.three@` — all `@transfleet.com`. Tokens last 15 minutes; log in again on a `401`.

Seeded ids:

| | Id |
|---|---|
| Warehouse KHI-01 (Karachi) | `ffd130be-7beb-5f93-8f02-c18d1ff15fda` |
| Warehouse LHE-01 (Lahore) | `30793e12-fb89-55bf-bdc0-11372ee8e56b` |
| SKU Wireless Keyboard | `6c5d7270-9a58-50d8-9717-3da9eaaa859c` |
| SKU USB-C Charger | `412af240-fcf2-5002-8222-ff855da05935` |

---

## 4 · A shipment from order to door, with notifications

### 4.1 Create it

- [ ] `POST /api/v1/shipments` (as admin):

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
  "items": [{ "sku_id": "6c5d7270-9a58-50d8-9717-3da9eaaa859c", "quantity": 2 }]
}
```

**→ `201`**, status `created`. Keep the `id` (**`{shipment_id}`** below) and the
`reference_no` (`SL-2026-…`).

- [ ] Mailpit → **"We've got your order — SL-2026-…"** to *Ayesha Khan*. Open it: HTML and
  plain-text versions.
- [ ] Terminal: `notification-celery | Task notification.send[…] succeeded … 'sent'`

### 4.2 Pack and dispatch

- [ ] `PATCH /api/v1/shipments/{shipment_id}/status` → `{ "status": "ready_for_dispatch" }`
  (leave the `version` box empty)
- [ ] `POST /api/v1/shipments/{shipment_id}/packages` → `{ "weight_g": 1500 }`
- [ ] `POST /api/v1/shipments/{shipment_id}/dispatch` (no body) → **`202`**, `dispatching`
- [ ] `GET /api/v1/dispatches/dispatch-{shipment_id}-1` → `"status": "completed"`, seven steps
- [ ] `GET /api/v1/shipments/{shipment_id}` → `dispatched`. Keep its **`courier_id`**.

Messages from the dispatch:

- [ ] Mailpit → **"Your shipment SL-2026-… is on its way"**, with a tracking link. Open the
  link: the public lookup, no login needed.
- [ ] Terminal, the SMS and push mocks:

```
notification-celery | SMS to +923001234567: Hi Ayesha Khan, ... has left the warehouse ...
notification-celery | PUSH to <courier_id>: New job: SL-2026-... from KHI-01. Open the app to start.
notification-celery | SMS to <courier's phone>: New job: SL-2026-... from KHI-01. ...
```

### 4.3 Deliver it as the courier

- [ ] `GET /api/v1/couriers/{courier_id}` → `code` is `CR-0001` (log in as
  `courier.one@transfleet.com`) or `CR-0003` (`courier.three@transfleet.com`). Log in as
  that courier and **Authorize** with the new token.

Updates need fresh ids and times close to now, in order. This prints a ready body —
replace `PASTE-SHIPMENT-ID`:

```bash
python3 - <<'EOF'
import json, uuid, datetime as d
sid, now = "PASTE-SHIPMENT-ID", d.datetime.now(d.timezone.utc)
steps = ["picked_up", "out_for_delivery", "delivered"]
print(json.dumps({"updates": [{"event_id": str(uuid.uuid4()), "shipment_id": sid,
    "type": "milestone", "milestone": m,
    "occurred_at": (now + d.timedelta(minutes=i)).isoformat()}
    for i, m in enumerate(steps, 1)]}, indent=2))
EOF
```

- [ ] `POST /api/v1/tracking/updates/batch` with that body → `"recorded": 3`
- [ ] `GET /api/v1/shipments/{shipment_id}` (any token) → `delivered`

Messages from the delivery:

- [ ] Terminal: `SMS to +923001234567: …out for delivery…`, then the delivered SMS
- [ ] Mailpit → **"Delivered — SL-2026-…"**

### 4.4 The delivery log

Log in again as **admin** (or `support.lead@`) and Authorize.

- [ ] `GET /api/v1/notifications?shipment_id={shipment_id}` → 8 rows, all `sent`:

| template | channels |
|---|---|
| `shipment_confirmed` | email |
| `new_assignment` | push, sms (to the courier) |
| `shipment_dispatched` | email, sms |
| `out_for_delivery` | sms |
| `delivered` | email, sms |

- [ ] `GET /api/v1/notifications/{id}` on one → the full `body`, `html`,
  `provider_message_id`
- [ ] As the courier: `GET /api/v1/notifications/mine` → only their two `new_assignment`
  messages
- [ ] As the courier: `GET /api/v1/notifications` → **`403`** (staff only)

---

## 5 · The nine metrics

As admin:

- [ ] `GET /api/v1/analytics/summary` (no parameters = last 30 days):

| Field | Expect after Part 4 |
|---|---|
| `total_shipments` | 1 |
| `dispatched` · `delivered` | 1 · 1 |
| `failed_deliveries` · `failed_delivery_rate` | 0 · 0.0 |
| `average_delivery_hours` | a small number: creation to delivery took minutes |
| `returns` · `return_rate` | 0 · 0.0 |
| `courier_utilisation.active_couriers` | 5 (the active seeded couriers), `utilisation` 0.0 — the job is done |
| `warehouse_throughput` | one row, `KHI-01`, dispatched 1 |
| `failed_events` | `{ "dead_letters": 0, "dispatch_failures": 0 }` |

- [ ] `GET /api/v1/analytics/timeseries?metric=dispatched` → 30 points, today = 1, the rest 0
- [ ] `GET /api/v1/analytics/couriers` → your courier with `assigned: 1`, `delivered: 1`
- [ ] `GET /api/v1/analytics/warehouses` → KHI-01 with `dispatched_per_day`
- [ ] `GET /api/v1/analytics/timeseries?metric=delivery_seconds` → **`422`** (internal counter)
- [ ] `GET /api/v1/analytics/summary?from=2026-11-10&to=2026-11-01` → **`422`**

Scoping — log in as `wh.lahore@transfleet.com`:

- [ ] `/summary` → `total_shipments: 0` (the shipment was Karachi), `failed_events: null`
- [ ] `/summary?warehouse_id=ffd130be-7beb-5f93-8f02-c18d1ff15fda` → **`403`**
- [ ] `/issues` → **`403`**

Grafana:

- [ ] http://localhost:3000 → SmartLogistics → **Business KPIs** → the same numbers.

Rebuild, as admin:

- [ ] `POST /api/v1/analytics/recompute` → `{ "from": "2026-11-01", "to": "2026-11-30" }`
  → **`202`**. Terminal: `analytics-celery | rebuilt rollups 2026-11-01..2026-11-30 from N
  milestone(s)`. `/summary` gives the same numbers: the live counters already matched the
  facts.

> `/summary` answers are cached for 30 s. If a number hasn't moved yet, wait and retry.

---

## 6 · One trace, dashboards, alerts

### 6.1 The dispatch as one trace

- [ ] http://localhost:16686 → Service **shipment** → Operation
  `POST /api/v1/shipments/{shipment_id}/dispatch` → **Find Traces** → open the trace.

It spans several services. Expand it and look for:

- [ ] the HTTP request in **shipment**, then `publish shipment.events`
- [ ] `consume shipment.events` in **orchestrator**, then the Temporal workflow and its
  activities (`RunActivity:…`)
- [ ] the activities' HTTP calls into **shipment**, **inventory**, **courier**, **tracking**
- [ ] consumers in **inventory**, **courier**, **notification**, **analytics**
- [ ] `notification.send` from the Celery worker

- [ ] Copy the trace id. In the terminal, log lines from those services carry
  `trace_id=<the same id>`.

### 6.2 Dashboards

- [ ] Grafana → **API health**: requests/s per service; read and write p95 against the red
  150 ms / 300 ms lines; slowest routes.
- [ ] Grafana → **Event pipeline**: outbox age near 0; events/s per consumer and outcome
  (`processed`, `duplicate`, `ignored`); end-to-end lag p95 under the 5 s line; Celery
  tasks; notifications by channel; Temporal workflows.

### 6.3 An alert fires

- [ ] Ctrl+C `./dev.sh start`, then start everything except courier:
  `./dev.sh start --reload identity warehouse inventory shipment orchestrator tracking analytics notification`
- [ ] http://localhost:9090/alerts → **TargetDown** for courier: *pending*, then *firing*
  after about a minute.
- [ ] Ctrl+C and `./dev.sh start --reload` → it clears within a minute.

---

## 7 · A failed event: dead letter, then replay

Make inventory's consumer fail on purpose. Inventory keeps a copy of every warehouse, so
a warehouse change it can't save fails there.

- [ ] Stop inventory's database: `docker compose stop inventory-db`
- [ ] As admin: `PATCH /api/v1/warehouses/ffd130be-7beb-5f93-8f02-c18d1ff15fda`

```json
{ "name": "Karachi Hub (test)" }
```

- [ ] Over about 30 s, `inventory-worker` logs failed attempts, then:

```
inventory moved <event-id> (warehouse.updated) to dlq.inventory (handler_failed): ...
```

Shipment's and courier's replicas took the change normally.

- [ ] `./dev.sh dlq list` → one letter: `inventory`, `dead`, `warehouse.updated`, the error
- [ ] `GET /api/v1/analytics/issues?kind=dead_letter` → the same letter, `status: open`
- [ ] http://localhost:9090/alerts → **DeadLetters** firing
- [ ] Grafana → Event pipeline → *Dead letters* shows the spike

Fix and replay:

- [ ] `docker compose start inventory-db` and wait ~10 s
- [ ] `./dev.sh dlq replay inventory --all`
  → `replayed warehouse.updated <event-id> -> retry.inventory`
- [ ] Within ~30 s, inventory has the new name, *Karachi Hub (test)*:

```bash
docker compose exec inventory-db psql -U inventory -c "select code, name from warehouse_refs"
```

- [ ] `./dev.sh dlq list` → `replayed`; `/analytics/issues` → `status: replayed`

Only inventory received the replay: shipment and courier handled the event the first time.

Put the name back: `PATCH` with `{ "name": "Karachi Central Fulfilment Centre" }`.

---

## 8 · Sign-out takes effect everywhere

- [ ] Log in as `support.lead@transfleet.com`, Authorize.
- [ ] `GET /api/v1/shipments` → `200`
- [ ] `POST /api/v1/auth/logout` with `{}` (sign out of every session) → `200`
- [ ] `GET /api/v1/shipments` with the **same** token → **`401` "Token has been revoked."**
  This comes from the shipment service, not identity. Before Week 3 the token worked until
  it expired.
- [ ] `GET /api/v1/analytics/summary` with that token → `401` as well
- [ ] Log in again → the new token works.

Suspension behaves the same way:

- [ ] As admin, find support's id with `GET /api/v1/users?search=support`, then
  `PATCH /api/v1/users/{id}/status` → `{ "status": "suspended", "reason": "test" }`
- [ ] Support's token now gets `401` in every service.
- [ ] `PATCH …/status` → `{ "status": "active" }` to restore.

---

## 9 · Background jobs survive an outage

- [ ] `docker compose stop rabbitmq` (Celery workers log connection errors and retry)
- [ ] Create another shipment (4.1, with `"customer_reference": "ORD-WEEK3-2"`) → `201`.
  No email arrives.
- [ ] `GET /api/v1/notifications?status=pending` → its confirmation, `pending`
- [ ] `docker compose start rabbitmq`
- [ ] Within about a minute the overdue sweep re-queues it: the email lands in Mailpit and
  the row is `sent`.

---

## 10 · The hold sweeper

Holds for shipments that never got recorded expire after 30 minutes, and the sweep runs
every 5. To see it quickly, restart with shorter settings:

```bash
UNCONFIRMED_HOLD_MINUTES=1 HOLD_SWEEP_SECONDS=30 ./dev.sh start --reload
```

- [ ] As admin: `GET /api/v1/inventory?warehouse_id=ffd130be-7beb-5f93-8f02-c18d1ff15fda&sku_id=412af240-fcf2-5002-8222-ff855da05935`
  → note `reserved_qty`
- [ ] `POST /api/v1/inventory/reservations`, holding stock for a shipment that will never exist:

```json
{
  "shipment_id": "11111111-2222-3333-4444-555555555555",
  "warehouse_id": "ffd130be-7beb-5f93-8f02-c18d1ff15fda",
  "lines": [{ "sku_id": "412af240-fcf2-5002-8222-ff855da05935", "quantity": 3 }]
}
```

- [ ] The same `GET` → `reserved_qty` up by 3
- [ ] Within ~90 s: `inventory-celery | released 1 expired hold(s)`, and `reserved_qty` is
  back where it was.
- [ ] Ctrl+C and `./dev.sh start --reload` to return to the normal settings.

---

## 11 · Suppressions and resend

As admin or support:

- [ ] `POST /api/v1/notifications/suppressions` → **`201`**, keep its `id`:

```json
{ "channel": "email", "address": "ayesha.khan@example.com", "reason": "asked to stop" }
```

- [ ] The same request again → **`409`**
- [ ] Create another shipment for Ayesha (4.1, `"customer_reference": "ORD-WEEK3-3"`) →
  no email in Mailpit; `GET /api/v1/notifications?shipment_id=<new id>` → `suppressed`
- [ ] As support: `DELETE /api/v1/notifications/suppressions/{id}` → **`403`**; as admin →
  **`204`**
- [ ] Resend Part 4's delivered email: `POST /api/v1/notifications/{id}/resend` → **`202`**,
  a new row with `resend_of`, a second "Delivered" email in Mailpit
- [ ] Resend a `pending` message (if you have one from Part 9) → **`409`**

---

## If something doesn't happen

| Symptom | Check |
|---|---|
| No email at all | Is `notification-celery` running and RabbitMQ up? Rows wait as `pending` and send once it's back |
| Dispatch fails with `reference_in_use` right after a wipe | Tracking is reading a MongoDB the wipe didn't touch. Docker's MongoDB is on **27018** (27017 is left to any MongoDB on your Mac); check `MONGO_URL` isn't set to 27017 in your shell or `.env` |
| Messages for old shipments arrive | Kafka still had Week 2 events: redo Part 1 with `docker down -v` |
| `/summary` stays at 0 | Is `analytics-worker` running? The 30 s cache. `./dev.sh status` |
| Grafana panels empty | http://localhost:9090/targets; Business KPIs reads `analytics-db` directly |
| No cross-service trace | Jaeger must be up before `start`; `SL_TRACING` must not be `off` |
| `dlq list`: Kafka unavailable | `./dev.sh infra up` |
| Replay not picked up | Wait 30 s (a new topic is noticed on the next metadata refresh), then check `inventory-worker` |
| Errors mentioning the schema registry | http://localhost:8081/subjects; `SL_SCHEMA_REGISTRY=off ./dev.sh start` runs without it |
| Every request `401` after restarting identity | Log in again; tokens survive restarts, but if you regenerated keys all old tokens are invalid |
| Tests: `Temporal CLI` download fails | Set `TEMPORAL_CLI_PATH` to a local `temporal` binary (`brew install temporal`) |

When you report a failure, the most useful thing to paste is the failing test's summary
line or the terminal lines around the error, with their process prefix.
