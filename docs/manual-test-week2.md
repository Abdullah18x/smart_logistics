# Manual test — Week 2 (dispatch, courier, tracking, delivery)

Three scenarios you can run in Swagger (**http://localhost:8000/docs**), a few minutes each.

- **A** — a shipment dispatched and delivered
- **B** — no courier free: the saga undoes its work, then a retry succeeds
- **C** — duplicate prevention

> Tick the boxes as you go. Every request body below is ready to paste into Swagger's
> "Try it out" box — **replace** the example it fills in, which is only a placeholder.

---

## Setup

```bash
./dev.sh infra up        # databases, Kafka, Temporal (+UI), MongoDB, gateway
./dev.sh migrate         # courier is new; shipment has a new migration
./dev.sh seed            # warehouses, users, SKUs and stock, couriers
./dev.sh start --reload  # 7 APIs + 7 workers
```

Wait for `==> running: identity :8001, ... tracking :8007 (+ workers)`. Without the workers
no event moves. `seed` is safe to re-run.

**Log in:** `POST /api/v1/auth/login` with `admin@transfleet.com` / `SmartLogistics!2026`,
copy `access_token`, paste it into Swagger's **Authorize** button (top right).

| Open | Where |
|---|---|
| Swagger, all services | http://localhost:8000/docs |
| Temporal UI | http://localhost:8233 |
| Jaeger | http://localhost:16686 |

Seeded ids (same in every fresh database):

| | Id |
|---|---|
| Warehouse KHI-01 (Karachi) | `ffd130be-7beb-5f93-8f02-c18d1ff15fda` |
| Warehouse LHE-01 (Lahore) | `30793e12-fb89-55bf-bdc0-11372ee8e56b` |
| SKU Wireless Keyboard | `6c5d7270-9a58-50d8-9717-3da9eaaa859c` |
| SKU USB-C Charger | `412af240-fcf2-5002-8222-ff855da05935` |
| Courier CR-0002 (Lahore) | `a2a7e116-024a-5a7b-a5da-ed88f3b42df1` |

---

## A — dispatch and deliver

### A1 · Create the shipment

- [ ] `POST /api/v1/shipments`

```json
{
  "customer_reference": "ORD-MANUAL-1",
  "origin_warehouse_id": "ffd130be-7beb-5f93-8f02-c18d1ff15fda",
  "service_level": "express",
  "destination_address": {
    "contact_name": "Ayesha Khan",
    "contact_phone": "+923001234567",
    "line1": "House 12, Street 4, DHA Phase 6",
    "city": "Karachi",
    "country_code": "PK"
  },
  "items": [{ "sku_id": "6c5d7270-9a58-50d8-9717-3da9eaaa859c", "quantity": 2 }]
}
```

**→ `201`**, status `created`, a reference like `SL-2026-000001`. Keep the `id` — it is the
`{shipment_id}` everywhere below. Stock is held in inventory; the `shipment.created` event
confirms the hold a moment later.

### A2 · Pack it and dispatch it

- [ ] `PATCH /api/v1/shipments/{shipment_id}/status` → `{ "status": "ready_for_dispatch" }`
- [ ] `POST /api/v1/shipments/{shipment_id}/packages` → `{ "weight_g": 1500 }`
- [ ] `POST /api/v1/shipments/{shipment_id}/dispatch` *(no body)*

**→ `202`**, status `dispatching`, and a `dispatch_workflow_id` of
`dispatch-{shipment_id}-1`. The API did no work: it recorded the request as an event, and
the orchestrator started the workflow from it.

> Leave the `version` query box empty — that is the optimistic lock, for when you *want*
> the request rejected if someone else changed the shipment first.

### A3 · Watch the workflow

- [ ] `GET /api/v1/dispatches/{dispatch_workflow_id}`

**→** a second later, `"status": "completed"` and all seven steps:

```json
{
  "status": "completed",
  "done": ["validate_shipment", "confirm_stock", "generate_labels",
           "assign_courier", "start_tracking", "mark_dispatched", "start_delivery"],
  "result": { "status": "dispatched", "courier_id": "e0271f09-..." }
}
```

- [ ] Open http://localhost:8233 and click the workflow (see [Temporal](#temporal) below)

### A4 · Check what it changed in each service

- [ ] `GET /api/v1/shipments/{shipment_id}` → `dispatched`, with a `courier_id`
- [ ] `GET /api/v1/shipments/packages/{package_id}/label` → a real A6 PDF with a barcode
- [ ] `GET /api/v1/couriers/assignments?shipment_id=...` → one `active` assignment
- [ ] `GET /api/v1/tracking/journeys/{shipment_id}` → journey `active` at `dispatched`
- [ ] `GET /api/v1/inventory?warehouse_id=...&sku_id=...` → `on_hand_qty` down by 2

The stock moved because the shipment reached `dispatched` — inventory reacted to the event.

### A5 · Deliver it as the courier

The job went to a Karachi courier, CR-0001 or CR-0003. Check which with
`GET /api/v1/couriers/{courier_id}`, then log in as `courier.one@` or `courier.three@`
`transfleet.com` and re-Authorize in Swagger.

- [ ] `GET /api/v1/couriers/assignments/mine` → your shipment
- [ ] `POST /api/v1/tracking/updates` three times, as below

```json
{
  "event_id": "<a fresh uuid each time>",
  "shipment_id": "{shipment_id}",
  "type": "milestone",
  "milestone": "picked_up",
  "occurred_at": "2026-10-13T09:00:00Z",
  "location": { "latitude": 24.8607, "longitude": 67.0011 }
}
```

Then the same with `"out_for_delivery"`, then `"delivered"`.

**→ `202`** and `"status": "recorded"` each time.

Two things to get right:

- **a fresh `event_id` per update** (any uuid) — repeating one is treated as a retry;
- **`occurred_at` a few minutes apart, in order** — the journey only ever moves forward in
  time, so identical timestamps leave its milestone where it was.

Notice there is no `in_transit` update — the device "missed" it on purpose.

- [ ] `GET /api/v1/shipments/{shipment_id}/history`

**→** the workflow filled the gap, and wrote every status itself as `system`:

```
created             admin
ready_for_dispatch  admin
dispatching         admin
dispatched          system
picked_up           system
in_transit          system   <- nobody reported this one
out_for_delivery    system
delivered           system
```

- [ ] `GET /api/v1/tracking/journeys/{shipment_id}` → `completed` / `delivered`
- [ ] `GET /api/v1/couriers/assignments?shipment_id=...` → `completed`, capacity freed
- [ ] `GET /api/v1/tracking/public/{reference_no}` → the customer view, no token needed

---

## B — no courier: compensation, then retry

The best one to watch in the Temporal UI.

### B1 · Take Lahore offline

- [ ] `GET /api/v1/couriers?warehouse_id=30793e12-fb89-55bf-bdc0-11372ee8e56b&availability=available`
- [ ] For each one: `PUT /api/v1/couriers/{courier_id}/availability` →
      `{ "availability": "offline" }`

### B2 · Dispatch a Lahore shipment

- [ ] `POST /api/v1/shipments` with `origin_warehouse_id` LHE-01, SKU USB-C Charger, qty 1
- [ ] `PATCH .../status` → `ready_for_dispatch`
- [ ] `POST .../packages` → `{ "weight_g": 400 }`
- [ ] `POST .../dispatch`

### B3 · Watch it wait, then fail

- [ ] `GET /api/v1/dispatches/{dispatch_workflow_id}` a few times

**→** while it waits:

```json
{ "status": "running",
  "step": "waiting for a courier (2/5)",
  "done": ["validate_shipment", "confirm_stock", "generate_labels"] }
```

It tries 5 times, 60 s apart by default — about five minutes. To make it quick, restart with
`COURIER_WAIT_ATTEMPTS=3 COURIER_WAIT_SECONDS=5 ./dev.sh start --reload`.

When it gives up:

- [ ] `GET /api/v1/shipments/{shipment_id}` → `dispatch_failed`, `failure_reason`
      `"no_courier_available: No available courier with spare capacity in Lahore."`,
      no courier
- [ ] `GET /api/v1/inventory/reservations?shipment_id=...` → the hold is **still there**
      (`held`, with a `confirmed_at`) — kept on purpose, so a retry reuses it
- [ ] `GET /api/v1/tracking/journeys/{shipment_id}` → `404`: tracking was never started,
      so there was nothing to undo

### B4 · Retry

- [ ] `PUT /api/v1/couriers/a2a7e116-024a-5a7b-a5da-ed88f3b42df1/availability` →
      `{ "availability": "available" }`
- [ ] `PATCH .../status` → `ready_for_dispatch`
- [ ] `POST .../dispatch`

**→** `dispatch-{shipment_id}-**2**`, `dispatch_attempts: 2`, and this time it completes.
Both attempts now sit side by side in the Temporal UI.

---

## C — duplicate prevention

| Try | Expect |
|---|---|
| `POST /dispatch` twice quickly | `202`, then `409` — only `ready_for_dispatch` can dispatch |
| Stop the orchestrator worker, dispatch, start it | it runs when the worker is back — Kafka held it |
| The same `event_id` twice | `recorded`, then `duplicate` — one entry on the timeline |
| A `picked_up` dated before one already applied | recorded, but nothing moves backwards |
| `PATCH /status` → `delivered` as admin | `403` — only the workflow (`system`) writes those |

---

## Temporal

### The UI — http://localhost:8233

Click `dispatch-{shipment_id}-1`:

- **Input and Results** — what it started with and what it returned.
- **History** — one `ActivityTaskScheduled → Started → Completed` trio per step, then
  `StartChildWorkflowExecutionInitiated` for the delivery workflow. Click an activity to see
  its input, result and attempts.
- On the **failed** one from B: `assign_courier` appears three times, with
  `TimerStarted`/`TimerFired` between the tries (the durable waits), then
  `mark_dispatch_failed`. The message is the courier service's own, carried through unchanged.
- `delivery-{shipment_id}` runs separately, for as long as the delivery takes. Each courier
  update arrives as `WorkflowExecutionSignaled`. Its **Queries** tab answers `state` with the
  current status, the statuses applied and the attempt count.

### From the API

`GET /api/v1/dispatches/{workflow_id}` — current step while running, result once finished.

### From the CLI (`brew install temporal`)

```bash
temporal workflow list --address localhost:7233
temporal workflow show --workflow-id dispatch-<shipment>-1 --address localhost:7233
temporal workflow query --workflow-id delivery-<shipment> --type state --address localhost:7233
```

---

## What the logs look like

`./dev.sh start` prefixes each line with the service, so one dispatch reads as a story:

```
shipment      | "POST  /api/v1/shipments/<id>/dispatch"  202   <- you
identity      | "POST  /api/v1/auth/service-token"       200   <- orchestrator's system token
shipment      | "POST  /api/v1/shipments/<id>/labels"    200   <- generate_labels
courier       | "POST  /api/v1/couriers/assignments"     201   <- assign_courier
tracking      | "POST  /api/v1/tracking/journeys"        201   <- start_tracking
shipment      | "PATCH /api/v1/shipments/<id>/status"    200   <- mark_dispatched
```

The service token is cached for five minutes, so that second line only appears on the first
dispatch after a restart.

Scenario B shows the retries plainly:

```
courier       | "POST  /api/v1/couriers/assignments"     409   <- try 1
courier       | "POST  /api/v1/couriers/assignments"     409   <- try 2
courier       | "POST  /api/v1/couriers/assignments"     409   <- try 3
shipment      | "PATCH /api/v1/shipments/<id>/status"    200   <- mark_dispatch_failed
```

From the orchestrator worker:

```
orchestrator worker started on task queue smartlogistics-dispatch
started dispatch-<id>-1
dispatch <id> already started; ignoring the duplicate request
no running delivery workflow for <id>
```

---

## If nothing happens after the 202

The request travels shipment → outbox → Kafka → orchestrator. Check in that order:

1. **Workers running?** `./dev.sh start` runs one per service. Without them the event sits
   in the outbox.
2. **Kafka up?** `./dev.sh infra up`. Workers retry while it is down and say so; nothing is lost.
3. **Temporal up?** `./dev.sh status`, and http://localhost:8233. The orchestrator worker
   logs `orchestrator worker started on task queue ...` once connected.
4. **Database migrated?** A worker started before `./dev.sh migrate` logs
   `database not migrated (missing outbox_events, ...) — waiting`, and starts by itself
   once you migrate.
5. **Still nothing?** Look in the outbox:
   ```bash
   psql postgresql://shipment:shipment@localhost:5444/shipment \
     -c "select event_type, published_at from outbox_events order by seq desc limit 5"
   ```
   `published_at = null` means the relay has not published it yet.

## Endpoints that are not yours to call

These exist because the services call each other over HTTP, and admins need them for
recovery. Calling them by hand mid-test breaks the flow — committing stock early, for
example, makes the next dispatch fail with `stock_not_held`.

`POST /inventory/reservations/{id}/confirm` · `/commit` · `/release` ·
`POST /shipments/{id}/labels` · `POST /tracking/journeys` · `POST /couriers/assignments`
