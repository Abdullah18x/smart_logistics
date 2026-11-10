# SmartLogistics — Microservices

Backend for TransFleet's supply-chain and delivery orchestration platform, rebuilt
from the Phase 1 modular monolith into independently deployable services. The
monolith lives on in git history (the `phase_1` branch) for reference.

Each service owns an **isolated database instance**, verifies tokens on its own,
and talks to the others only through **REST** (immediate questions), **Kafka
events** (facts others react to) and **Temporal workflows** (multi-step processes
that must finish or be undone: dispatch and delivery). Background jobs run on
**Celery**; every hop is measured (Prometheus, Grafana) and traced (Jaeger).

## Architecture

```mermaid
flowchart TB
    client([Ops web · warehouse app · courier app · public tracking])
    gw[Gateway · Traefik<br/>routes /api/v1/&lt;domain&gt;]
    client --> gw

    subgraph services[Services — each with its own database]
        id[identity<br/>users · sessions · JWKS · service accounts]
        wh[warehouse<br/>facilities · zones · hours]
        inv[inventory<br/>SKUs · stock · holds · ledger]
        shp[shipment<br/>shipments · parcels · labels · history]
        cr[courier<br/>couriers · assignments]
        trk[tracking<br/>journeys · field updates]
        orc[orchestrator<br/>Temporal worker · no DB]
        an[analytics<br/>facts · daily rollups · issues]
        nt[notification<br/>delivery log · suppressions]
    end
    gw --> id & wh & inv & shp & cr & trk & orc & an & nt

    id --- iddb[(identity-db)]
    wh --- whdb[(warehouse-db)]
    inv --- invdb[(inventory-db)]
    shp --- shpdb[(shipment-db)]
    cr --- crdb[(courier-db)]
    trk --- mongo[(MongoDB)]
    orc --- temporal[[Temporal]]
    an --- andb[(analytics-db)]
    nt --- ntdb[(notification-db)]
    nt -- SMTP --> mail[[Mailpit]]

    shp -- "REST: reserve stock" --> inv
    id -- "REST: does warehouse exist?" --> wh
    orc -- "REST as service account:<br/>validate · confirm · labels · assign · journey · status" --> shp & inv & cr & trk

    kafka{{Kafka · *.events}}
    id & wh & inv & shp & cr & trk -- outbox relay --> kafka
    kafka -- "shipment.dispatch_requested<br/>tracking.milestone_recorded" --> orc
    kafka -- "shipment.* · courier.* · dlq.*" --> an
    kafka -- "shipment.* · courier.*" --> nt
```

| Service | Owns | Publishes | Consumes | Calls |
|---|---|---|---|---|
| **identity** | users, refresh tokens, operator↔warehouse scopes, service clients | `user.*` | `warehouse.deleted` (drop scopes), `courier.*` (link logins) | warehouse |
| **warehouse** | warehouses, zones, operating hours | `warehouse.*` (full snapshots) | — | — |
| **inventory** | SKUs, stock positions, holds, stock ledger, warehouse replica | `stock.*` | `warehouse.*`, `shipment.created`, `shipment.status_changed` | — |
| **shipment** | shipments, items, parcels, PDF labels, status history, warehouse replica | `shipment.*` incl. `dispatch_requested` | `warehouse.*`, `courier.assigned/unassigned` | inventory |
| **courier** | couriers, assignments, warehouse replica | `courier.*` incl. `assigned/unassigned` | `warehouse.*`, `shipment.status_changed` (end assignments) | — |
| **tracking** | journeys and field updates (MongoDB) | `tracking.milestone_recorded` | `courier.assigned`, `shipment.status_changed` (returns) | — |
| **orchestrator** | workflow state (in Temporal) | — | `shipment.dispatch_requested`, `tracking.milestone_recorded` | shipment, inventory, courier, tracking |
| **analytics** | milestone facts, daily rollups, courier load, pipeline issues | — | `shipment.*`, `courier.*`, every `dlq.*` | — |
| **notification** | delivery log, suppressions, recipient and courier contacts | — | `shipment.*`, `courier.*` | SMTP (Mailpit), mock SMS / push |

The AI assistant and indexer come in Weeks 4–5. The full plan is in the
*Microservices Migration Plan* doc.

## How the hard parts work

**Authentication.** Identity signs RS256 access tokens whose claims carry role,
warehouse scope and courier id. Every other service fetches Identity's public key
from `/.well-known/jwks.json` once, caches it, and verifies tokens locally. Background
work uses a **service account**: the orchestrator exchanges its client id and secret
for a 5-minute `system` token at `/api/v1/auth/service-token`, renewed automatically.

**Creating a shipment (reserve, then record).** Shipment checks the origin warehouse
against its local replica, calls Inventory to hold the stock, then records the
shipment, items, history and a `shipment.created` outbox row in one transaction.
Inventory confirms the hold from that event; a failed record releases the hold.

**Dispatching (Temporal saga).**
1. `POST /api/v1/shipments/{id}/dispatch` moves a `ready_for_dispatch` shipment with
   at least one package to `dispatching` and stages `shipment.dispatch_requested`.
   A second click gets `409`.
2. The orchestrator starts `DispatchWorkflow` (id `dispatch-{shipment}-{attempt}`;
   Temporal rejects a duplicate) and runs: validate → confirm the stock hold → print
   PDF labels → **assign a courier** (same city, home warehouse first, least loaded;
   waits and retries when nobody is free) → open the tracking journey → mark
   `dispatched`. Inventory commits the stock when it sees `dispatched`.
3. Any step that fails for good undoes the earlier ones in reverse (close the journey,
   release the courier) and marks the shipment `dispatch_failed` with the reason. The
   stock hold is kept; set it `ready_for_dispatch` and dispatch again (attempt 2).

**Delivering.** The dispatch workflow hands over to `DeliveryWorkflow`. Couriers post
updates to tracking (one at a time or a batch after being offline, each with its own
`event_id`, so retries are harmless). Tracking forwards milestones to the workflow,
which ignores duplicates and stale reports, fills gaps (`out_for_delivery` straight
after `dispatched` applies `picked_up` and `in_transit` first), and is the only writer
of post-dispatch status — those statuses are reserved to the `system` role. An overdue
pickup is reassigned to another courier; after the last failed attempt a return starts.

**Consistency.** Every event is written to an outbox in the same transaction as the
change (in tracking, each event document is its own outbox entry) and relayed to
Kafka. Consumers record the event id in the same transaction as their effect, so
redelivery is a no-op. Write endpoints accept `Idempotency-Key`. Shipments use real
optimistic locking; stock rows are locked in id order; couriers are picked with
`FOR UPDATE SKIP LOCKED` and a partial unique index allows one active assignment per
shipment.

**Failed events.** Every event type has a JSON Schema contract, checked when it is
written and registered in the Schema Registry when published. A consumer that cannot
handle an event after 5 tries — or gets one that breaks its contract — moves it to
`dlq.<consumer>`; `./dev.sh dlq list` shows them and `./dev.sh dlq replay <consumer>`
sends them back to that consumer only.

**Notifications and analytics.** Both are consumers. Notification writes one row per
message (a redelivered event adds nothing) and a Celery task sends it — email to
Mailpit, SMS and push to logged mocks — retrying with backoff. Analytics stores each
milestone as a fact and bumps daily counters in the same transaction; a nightly
Celery job rebuilds the counters from the facts.

**Sign-out means now.** Sign-out, suspension and role changes write a Redis denylist
that every service checks, so an access token stops working on the next request.

**No foreign keys across services.** References are plain ids. Display fields are
snapshotted; cascades became events.

## Run it

One developer CLI does everything, on every OS.

**Prerequisites:** [uv](https://docs.astral.sh/uv/getting-started/installation/)
(it installs Python for you) and Docker Desktop, or Docker Engine with the Compose
plugin. Free ports: 5441–5447, 7233, 8233, 27017, 29092, 16686, 5672/15672, 6379,
8081, 9090, 3000, 1025/8025, and 8001–8009 (option A) or 8000/8080 (option B).

| macOS / Linux | Windows PowerShell | Windows cmd |
|---|---|---|
| `./dev.sh <command>` | `.\dev.ps1 <command>` | `dev <command>` |

### Option A — services on your machine, infrastructure in Docker (best for coding)

```bash
./dev.sh setup        # install the workspace + generate Identity's signing key
./dev.sh infra up     # 7 Postgres (5441-5447), Kafka, Schema Registry, Temporal, MongoDB, RabbitMQ,
                      # Redis, Mailpit, Prometheus, Grafana, Jaeger, gateway (8000)
./dev.sh migrate      # every SQL service; or: ./dev.sh migrate shipment courier
./dev.sh seed         # warehouses, users, SKUs and stock, couriers — in the right order
./dev.sh start        # 9 APIs (8001-8009) + workers + Celery workers; Ctrl+C stops all
```

The gateway runs on :8000 and forwards to the services on your machine, so
`http://localhost:8000` and the Swagger UI at `/docs` work in both options.

`start --reload` restarts a service when its code changes, `start shipment courier`
runs a subset, and `start --no-workers` skips the workers (no events, no workflows).
`./dev.sh status` shows every service's health; `./dev.sh reset` rolls back,
migrates and re-seeds.

### Option B — everything in Docker

```bash
./dev.sh docker up    # builds the images; databases, Kafka, Temporal, MongoDB, gateway on :8000
./dev.sh docker seed
./dev.sh docker logs orchestrator   # docker ps | migrate | down [-v]
```

### Try a dispatch

```bash
login() { curl -s localhost:8000/api/v1/auth/login -H 'content-type: application/json' \
  -d "{\"email\":\"$1\",\"password\":\"SmartLogistics!2026\"}" | jq -r .access_token; }
ADMIN=$(login admin@transfleet.com)
# create a shipment in Swagger (or docs/curl), then, with its id in $SID:
curl -s -X PATCH localhost:8000/api/v1/shipments/$SID/status -H "Authorization: Bearer $ADMIN" \
  -H 'content-type: application/json' -d '{"status":"ready_for_dispatch"}'
curl -s -X POST localhost:8000/api/v1/shipments/$SID/packages -H "Authorization: Bearer $ADMIN" \
  -H 'content-type: application/json' -d '{"weight_g":1500}'
curl -s -X POST localhost:8000/api/v1/shipments/$SID/dispatch -H "Authorization: Bearer $ADMIN" | jq
```

Watch the workflow at http://localhost:8233, or `GET /api/v1/dispatches/{workflow_id}`.
Then sign in as the assigned courier (`courier.one@` or `courier.three@` for Karachi)
and post `picked_up`, `out_for_delivery`, `delivered` to `/api/v1/tracking/updates`.

## API docs

**All APIs in one place: http://localhost:8000/docs** — one Swagger UI with a dropdown
per service. Authorize once and the token is kept as you switch services.

| URL / file | What |
|---|---|
| http://localhost:8000/docs | Swagger UI for every service, behind the gateway |
| http://localhost:8000 | Gateway: every API under `/api/v1/...` |
| http://localhost:800{1..9}/docs | One service's own Swagger UI, bypassing the gateway |
| `docs/curl/index.html` | Offline reference: every endpoint (95) with a copy-ready curl command |
| `docs/curl/openapi.json` | All specs merged into one, for Postman, Insomnia or Bruno |
| `docs/curl/*.postman_*.json` | Postman collection (a folder per service) and environment |
| http://localhost:8233 | Temporal UI: every dispatch and delivery workflow, step by step |
| http://localhost:8080 | Traefik dashboard |
| http://localhost:16686 | Jaeger traces — one trace from the HTTP call through Kafka, Celery and Temporal |
| http://localhost:3000 | Grafana: API health, event pipeline, business KPIs (anonymous viewer; admin/admin) |
| http://localhost:9090/alerts | Prometheus alert rules and their state |
| http://localhost:8025 | Mailpit: every email the platform sent |
| http://localhost:15672 | RabbitMQ (guest/guest): Celery queues |
| http://localhost:8081/subjects | Schema Registry: the registered event contracts |
| `docker compose --profile tools up -d kafka-ui` → :8090 | Kafka UI |

Seeded accounts (password `SmartLogistics!2026`): `admin@`, `support.lead@`,
`wh.karachi@`, `wh.lahore@`, `wh.regional@`, `courier.one@` … `courier.four@`
`@transfleet.com`. Couriers CR-0001/CR-0003 (Karachi) and CR-0002 (Lahore) are available.

## Develop and test

```bash
./dev.sh test          # platform unit tests, each service's suite, cross-service e2e
./dev.sh test courier  # one service
./dev.sh test --cov    # everything, with combined coverage and the gate (as CI runs it)
./dev.sh lint          # ruff + import-linter (services may never import each other)
./dev.sh check         # lint + `alembic check` per SQL service
```

Tests need the infrastructure from `./dev.sh infra up`: Postgres (or one server via
`SL_PG_HOST=localhost:5432`), MongoDB on `MONGO_URL` for tracking and e2e, and the
Temporal CLI for orchestrator and e2e — downloaded by the SDK on first run, or point
`TEMPORAL_CLI_PATH` at a `temporal` binary.

`tests/e2e` loads every service into one process, each on its own store, joined by an
in-memory event bus in place of Kafka, with a real Temporal dev server: it runs
dispatch and delivery end to end, including the no-courier failure and retry, and
checks the customer was notified and the metrics moved.

CI (`.github/workflows/ci.yml`) runs lint, migrations and `alembic check`, then
`./dev.sh test --cov` against PostgreSQL and MongoDB service containers; the build fails
below the coverage floor in `pyproject.toml`.

## Command reference

Swap `./dev.sh` for `.\dev.ps1` (PowerShell) or `dev` (cmd). `[svc ...]` means any
of `identity warehouse inventory shipment courier orchestrator tracking analytics notification`.

| Command | What it does | make alias |
|---|---|---|
| `setup` | `uv sync --all-packages` + generate the signing key | `make setup` |
| `keys` | generate `infra/keys/jwt-private.pem` (skipped if it exists) | `make keys` |
| `infra up` / `down` / `status` | start / stop / list databases, Kafka, Temporal, MongoDB, Jaeger, gateway, Swagger UI | `make infra-up` / `infra-down` |
| `migrate [svc ...]` | `alembic upgrade head` per SQL service | `make migrate s="shipment"` |
| `migrate --down [svc ...]` | roll every migration back | — |
| `seed` | seed warehouse → identity → inventory → courier | `make seed` |
| `reset` | migrate down, migrate up, seed | `make reset` |
| `start [svc ...]` | APIs on 8001–8009 + workers + Celery workers (with beat); `--reload`, `--no-workers` | `make start s=shipment` |
| `status` | `/health` of every service | `make status` |
| `docs` | regenerate `docs/curl` and `docs/database.md` from the code | `make docs` |
| `docker up` / `down [-v]` / `ps` / `logs [svc]` / `migrate` / `seed` | the whole stack in Docker | `make up` … |
| `test [svc ...] [--cov]` | platform tests, service suites, e2e (only when no service is named); `--cov` measures and gates | `make test s=shipment` |
| `dlq list [consumer]` / `dlq replay <consumer> --all \| <event-id>…` | inspect / replay dead letters | — |
| `lint` / `check` | ruff + import-linter / plus `alembic check` | `make lint` / `make check` |

## Configuration

Each service reads environment variables (or a `.env` file — see `.env.example`).
You normally set none of them: the defaults point at the Compose ports, and
`./dev.sh start` fills in the rest per service.

| Variable | Used by | Default / meaning |
|---|---|---|
| `DATABASE_URL` | SQL services | its own database on `localhost:5441`–`5447` |
| `KAFKA_BOOTSTRAP_SERVERS` | every service | `localhost:29092` (containers use `kafka:9092`) |
| `JWT_PRIVATE_KEY_FILE` | identity | `infra/keys/jwt-private.pem` (a Docker secret in Compose) |
| `JWKS_URL` | all but identity | `http://localhost:8001/.well-known/jwks.json` |
| `SERVICE_CLIENTS` | identity | JSON `{"client_id": "secret"}` of service accounts |
| `SERVICE_CLIENT_ID` / `SERVICE_CLIENT_SECRET` | orchestrator | its service account |
| `*_SERVICE_URL` | callers | e.g. `INVENTORY_SERVICE_URL=http://localhost:8003` |
| `TEMPORAL_ADDRESS` / `TASK_QUEUE` | orchestrator | `localhost:7233` / `smartlogistics-dispatch` |
| `COURIER_WAIT_ATTEMPTS` / `COURIER_WAIT_SECONDS` | orchestrator | look for a courier 5 times, 60 s apart, before failing the dispatch |
| `PICKUP_TIMEOUT_SECONDS` / `MAX_DELIVERY_ATTEMPTS` | orchestrator | reassign after this long without pickup / return after this many failures |
| `MONGO_URL` / `MONGO_DATABASE` | tracking | `mongodb://localhost:27017` / `tracking` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | every service | unset = no tracing export (`./dev.sh start` sets Jaeger; `SL_TRACING=off`) |
| `SCHEMA_REGISTRY_URL` | every service | unset = contracts checked locally only (`SL_SCHEMA_REGISTRY=off`) |
| `REDIS_URL` / `DENYLIST_FAIL_CLOSED` | every service | unset = no denylist or cache (`SL_REDIS=off`) / refuse tokens while Redis is down |
| `CELERY_BROKER_URL` | services with jobs | `amqp://guest:guest@localhost:5672//` |
| `METRICS_PORT` | workers, Celery workers | where `/metrics` is served (APIs use their own port) |
| `SMTP_HOST` / `SMTP_PORT` / `OPS_EMAIL` | notification | Mailpit `localhost:1025` / the operations mailbox |
| `RUN_MIGRATIONS` / `RUN_SEED` | container entrypoint | migrate / seed before starting |
| `SL_PG_HOST` | dev CLI and tests | e.g. `localhost:5432` to put every database on one server |
| `TEMPORAL_CLI_PATH` | tests | a local `temporal` binary instead of the SDK download |
| `SL_BIND_HOST` | `./dev.sh start` | `0.0.0.0` on Linux (so the gateway container can reach the APIs), `127.0.0.1` elsewhere |

## Troubleshooting

- **`./dev.sh: Permission denied`** — run `chmod +x dev.sh` once.
- **PowerShell refuses to run `dev.ps1`** — use `dev` (cmd wrapper) or
  `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.
- **"port is already allocated"** — another Postgres, Kafka, MongoDB or app is using
  the port; stop it.
- **`start` says an API port is taken** (`Address already in use`) — `start` names what
  holds it. A Docker container means the full Docker stack is still running its APIs (they
  restart with Docker): `./dev.sh infra up` removes them and keeps the infrastructure. A
  process is usually a server left over from an earlier run: `kill` every PID listed.
- **Every request is `401`** — the services couldn't fetch Identity's JWKS. Check
  `./dev.sh status`; if you regenerated the signing key, restart all services.
- **A dispatch stays in `dispatching`** — the orchestrator worker or Temporal isn't
  running (`start` warns when Temporal is down), or Kafka isn't, so the request event
  never arrived. Check `./dev.sh status` and http://localhost:8233.
- **Tracking returns 503** — MongoDB isn't running: `./dev.sh infra up`.
- **`localhost:8000` returns `502`** — the service behind that path isn't running.
- **Workers keep reconnecting** — Kafka isn't running: `./dev.sh infra up`.
- **No emails in Mailpit** — the notification Celery worker isn't running (RabbitMQ down?);
  messages wait as `pending` and the sweep sends them once it is back.
- **An event "disappeared"** — check `./dev.sh dlq list`; fix the cause and replay it.
- **Grafana panels are empty** — Prometheus scrapes the processes `./dev.sh start` runs;
  check http://localhost:9090/targets.
- **Want a clean slate** — `./dev.sh reset` (local) or `./dev.sh docker down -v`.

## Repository layout

```
libs/sl-platform/      shared plumbing only: auth, token denylist, cache, db, idempotency,
                       outbox, consumer, contracts, schema registry, dead letters, metrics,
                       telemetry, Celery factory, service client, app factory, worker runner
services/<name>/
  src/<name>/          config, db, models, schemas, repository, services/, api/,
                       events.py (publish + consume), contracts.py, main.py (HTTP),
                       worker.py, tasks.py (Celery, where there are jobs), seed.py
  migrations/          the service's own Alembic chain (SQL services)
  tests/
services/orchestrator/src/orchestrator/
  workflows/           DispatchWorkflow, DeliveryWorkflow (deterministic)
  activities.py        every call to another service
  trigger.py           Kafka → start workflows / signal deliveries
infra/gateway/ · infra/docker/ · infra/temporal/   gateway routes, Dockerfile, Temporal config
infra/prometheus/ · infra/grafana/                  scrape configs, alert rules, dashboards
scripts/               dev CLI, API and database doc generators
docker-compose.yml     databases, Kafka, Schema Registry, Temporal, MongoDB, RabbitMQ, Redis, Mailpit,
                       Prometheus, Grafana, Jaeger, gateway, Swagger UI, APIs, workers, Celery
.github/workflows/     CI: lint, migrations, tests, coverage gate
tests/e2e/             cross-service flows, real workflows
docs/                  architecture, PRD, database reference, ADRs, build logs
```

## Documentation

Start with [docs/documentation-guide.pdf](docs/documentation-guide.pdf): one page that says
where to look for what.

| Document | What's there |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Services, communication, data, consistency, security, workflows; §1.1 maps every requirement to its status |
| [docs/PRD.md](docs/PRD.md) | Use cases, requirements, milestones, traceability matrix |
| [docs/database.md](docs/database.md) | Every service's tables and the tracking collections — generated by `./dev.sh docs` |
| [docs/adr/](docs/adr/) | 23 architecture decision records |
| [docs/phase_1.md](docs/phase_1.md) · [phase_2.md](docs/phase_2.md) · [phase_3.md](docs/phase_3.md) | Week 1, 2 and 3 build logs |
| [docs/manual-test-week2.md](docs/manual-test-week2.md) | Run dispatch and delivery by hand: three scenarios, what to watch in the Temporal UI, what the logs look like |
| [docs/manual-test-week3.md](docs/manual-test-week3.md) | Week 3 by hand: notifications in Mailpit, metrics, dashboards, one trace, dead letters and replay, instant sign-out |
| [docs/curl/](docs/curl/) | Generated API collection (see [API docs](#api-docs)) |
| [docs/projectDocs/](docs/projectDocs/) | The assignment briefs, unmodified |

## Tech stack

Python 3.13 · FastAPI · SQLAlchemy 2 (async) · PostgreSQL 16 (one per service) ·
Alembic · Kafka (KRaft) via aiokafka · Temporal · MongoDB 7 (PyMongo async) ·
PyJWT (RS256/JWKS) · Argon2 · httpx · ReportLab (labels) · Traefik ·
Celery + RabbitMQ · Confluent Schema Registry (JSON Schema) · Redis · Jinja + aiosmtplib
(Mailpit) · Prometheus + Grafana · OpenTelemetry → Jaeger · uv workspace · Ruff ·
import-linter · pytest + coverage · GitHub Actions · Docker Compose.
Coming next: LangGraph + Qdrant.
