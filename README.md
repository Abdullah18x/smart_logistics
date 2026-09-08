# SmartLogistics — Backend

Intelligent Supply Chain & Delivery Orchestration Platform for TransFleet.

A logistics backend covering the shipment lifecycle: warehouse and inventory operations,
dispatch orchestration, courier assignment, tracking, returns and analytics — followed by
a GenAI layer for operational insights.

> **Status: Week 1 of 5** — foundation, data model and core services.
> The tree contains only what is currently built. See
> [`docs/ARCHITECTURE.md` §16.2](docs/ARCHITECTURE.md) for folders added in later weeks.

---

## Architecture

Solid lines are built and running today; dashed lines are designed and scheduled for later weeks
([ARCHITECTURE.md §1.1](docs/ARCHITECTURE.md) has the week-by-week mapping).

```mermaid
graph TB
    subgraph Clients["Clients"]
        OPS["Support & Admin"]
        WH["Warehouse Operator"]
        CO["Courier"]
    end

    subgraph API["SmartLogistics API — FastAPI"]
        CTRL["Controllers<br/>routing, RBAC, row scoping"]
        SVC["Services<br/>business logic, transactions"]
        REPO["Repositories<br/>data access"]
        CTRL --> SVC --> REPO
    end

    subgraph Async["Async processes — weeks 2 and 3"]
        TW["Temporal workers<br/>dispatch, delivery"]
        KC["Kafka consumers<br/>event fan-out"]
        CW["Celery workers<br/>notifications, analytics"]
    end

    PG[("PostgreSQL 16<br/>system of record<br/>7 schemas · 18 tables")]
    MG[("MongoDB<br/>events, audit")]
    RD[("Redis<br/>cache, locks")]

    OPS --> CTRL
    WH --> CTRL
    CO --> CTRL

    REPO --> PG
    SVC -.-> RD
    SVC -.-> TW
    PG -.->|outbox| KC
    KC -.-> CW
    KC -.-> MG
    TW -.-> PG

    classDef planned stroke-dasharray: 5 5
    class TW,KC,CW,MG,RD planned
```

The API is a **modular monolith with process-type separation**: one codebase and one image, with
separate entrypoints for the API, workflow workers and consumers, so background load can never
starve request handling. [ADR-001](docs/ARCHITECTURE.md) explains why this rather than microservices.

---

## Tech stack

| Layer | Technology |
|---|---|
| API | Python 3.12+, FastAPI, Pydantic v2 |
| Database | PostgreSQL 16 (system of record), SQLAlchemy 2.0 async, Alembic |
| Event store | MongoDB (tracking events, audit trail) |
| Cache / locks | Redis |
| Orchestration *(week 2)* | Temporal |
| Events *(week 3)* | Kafka + Schema Registry, Celery + RabbitMQ |
| Observability *(week 3)* | OpenTelemetry, Prometheus, Grafana, Jaeger |
| GenAI *(weeks 4–5)* | LangGraph, vector database, LLM provider |
| Tooling | uv, Ruff, pytest |
| Packaging | Docker (multi-stage), Docker Compose, Kubernetes via Kustomize |

---

## Quick start with Docker

The whole stack in one command — no Python, Postgres or Redis needed on your machine. Works
identically on macOS, Linux and Windows:

```bash
make docker-up      # Postgres, Redis, migrations, API on :8000
make docker-seed    # development data
```

Open <http://localhost:8000/docs> and log in as `admin@transfleet.com` / `SmartLogistics!2026`.
`make docker-down` stops it; add `v=1` to delete the data volumes.

Postgres is published on **5433** and Redis on **6380** so the stack runs alongside any you already
have on the usual ports. Full detail, plus Kubernetes for AWS and Azure, is in
[deploy/README.md](deploy/README.md).

To run everything natively instead, carry on below.

---

## Prerequisites

*(Only for running natively. The [Docker path](#quick-start-with-docker) needs none of this —
on Windows especially, it is the shortest route.)*

| Requirement | Notes |
|---|---|
| **Python 3.12+** | Developed against 3.13 |
| **PostgreSQL 16** | The system of record |
| **[uv](https://docs.astral.sh/uv/)** | Dependency manager — `pip install uv` |

> **Redis and MongoDB are not needed yet.** Both appear in `.env.example` and the Docker stack
> because Weeks 2–3 use them, but **no application code reads either one today**. Skip them: the
> API and all 701 tests run on PostgreSQL alone. Install them when Week 2 starts.

---

### macOS

```bash
brew install postgresql@16
brew services start postgresql@16

# postgresql@16 is keg-only — put its client tools on PATH
echo 'export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH"' >> ~/.zshrc
exec zsh
```

If `brew services start postgresql@16` fails with `Bootstrap failed: 5: Input/output error`,
the data directory was never initialised:

```bash
brew services stop postgresql@16
initdb --locale=C -E UTF-8 /opt/homebrew/var/postgresql@16
brew services start postgresql@16
```

On an Intel Mac, Homebrew lives under `/usr/local` rather than `/opt/homebrew` — adjust both
paths accordingly.

---

### Linux — Debian / Ubuntu

Ubuntu 24.04 carries PostgreSQL 16 directly. On 22.04 or older, add the PGDG repository first:

```bash
sudo apt install -y curl ca-certificates
sudo install -d /usr/share/postgresql-common/pgdg
sudo curl -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
  https://www.postgresql.org/media/keys/ACCC4CF8.asc

# One line: a continuation inside the quotes would put stray spaces in the source entry
echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" | sudo tee /etc/apt/sources.list.d/pgdg.list

sudo apt update
```

Then:

```bash
sudo apt install -y python3 python3-venv postgresql-16
sudo systemctl enable --now postgresql

# a Postgres role matching your Linux user, so `createdb` works without sudo
sudo -u postgres createuser --superuser "$(whoami)"

# and a password on it — see the note below, this step is easy to miss
sudo -u postgres psql -c "ALTER ROLE \"$(whoami)\" WITH PASSWORD 'smartlogistics';"
```

> **Why the password, when `createdb` works without one.** Command-line tools reach Postgres over
> a Unix socket, where Debian and Ubuntu default to `peer` authentication — your Linux username is
> the proof. The application connects over TCP to `localhost:5432`, where the default is
> `scram-sha-256`, which needs a real password. Skip this step and `createdb` succeeds while
> `alembic upgrade head` fails with `password authentication failed`.

---

### Linux — Fedora / RHEL

Fedora ships PostgreSQL 16 in its own repositories (16.11 on Fedora 41), so no extra repo is needed:

```bash
sudo dnf install -y python3 postgresql-server postgresql
sudo postgresql-setup --initdb
sudo systemctl enable --now postgresql

sudo -u postgres createuser --superuser "$(whoami)"
sudo -u postgres psql -c "ALTER ROLE \"$(whoami)\" WITH PASSWORD 'smartlogistics';"
```

On **RHEL, Rocky or AlmaLinux**, the default stream is usually older. Add the
[PGDG repository](https://www.postgresql.org/download/linux/redhat/) and install
`postgresql16-server` instead; the initdb helper is then `/usr/pgsql-16/bin/postgresql-16-setup
initdb` and the service is `postgresql-16`.

> **If the application cannot connect but `psql` can**, the TCP rules in `pg_hba.conf` are the
> reason — Red Hat family defaults are stricter than Debian's. Set them to password auth:
>
> ```bash
> sudo sed -i -E 's/^(host.*127\.0\.0\.1\/32\s+)\w+$/\1scram-sha-256/' /var/lib/pgsql/data/pg_hba.conf
> sudo systemctl restart postgresql
> ```

---

### Windows

Three options, easiest first.

**1. Docker Desktop — recommended.** Nothing else to install, and it is the same stack that
deploys. Install [Docker Desktop](https://docs.docker.com/desktop/install/windows-install/), then:

```powershell
docker compose up -d --build
docker compose --profile seed up seed
```

**2. WSL2** — a real Linux environment, and `make` works. In PowerShell:

```powershell
wsl --install -d Ubuntu
```

Then open Ubuntu and follow the **Debian / Ubuntu** instructions above. Keep the repository inside
the WSL filesystem (`~/smart_logistics`, not `/mnt/c/...`) — file watching for `--reload` is slow
and unreliable across the Windows/Linux boundary.

**3. Native Windows.** Works, with two caveats noted below.

```powershell
winget install Python.Python.3.13
winget install PostgreSQL.PostgreSQL.16
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Add PostgreSQL's client tools to `PATH` (adjust if you installed elsewhere):

```powershell
$env:Path += ";C:\Program Files\PostgreSQL\16\bin"
```

> **Two caveats on native Windows.**
> 1. Scripts live in `.venv\Scripts\`, not `.venv/bin/` — every command in this README that
>    starts `.venv/bin/` becomes `.venv\Scripts\`.
> 2. **`make` targets do not work.** The `Makefile` assumes a Unix shell and `.venv/bin/` paths.
>    Run the underlying commands directly, or use WSL2 or Docker where `make` works normally.

---

## Setup

Steps 1–4 are identical everywhere; only the virtualenv path differs at the end.

```bash
# 1. clone and enter the project
git clone git@github.com:Abdullah18x/smart_logistics.git
cd smart_logistics

# 2. create the database
createdb smartlogistics

# 3. install dependencies into a virtual environment
uv venv
uv pip install -e ".[dev]"

# 4. configure the environment
cp .env.example .env        # Windows PowerShell: Copy-Item .env.example .env
#    then set DATABASE_URL to match your machine
```

**macOS / Linux**

```bash
.venv/bin/alembic upgrade head          # create the tables
.venv/bin/python -m app.seeders.runner  # load development data
```

**Windows (PowerShell)**

```powershell
.venv\Scripts\alembic upgrade head
.venv\Scripts\python -m app.seeders.runner
```

Activate the environment to drop the prefix from every command below:

| Platform | Command |
|---|---|
| macOS / Linux | `source .venv/bin/activate` |
| Windows PowerShell | `.venv\Scripts\Activate.ps1` |
| Windows CMD | `.venv\Scripts\activate.bat` |

If PowerShell refuses the activation script, allow signed local scripts once:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

**`DATABASE_URL`** follows the same shape everywhere — only the credentials change:

```
postgresql+asyncpg://USER:PASSWORD@localhost:5432/smartlogistics
```

| Platform | Typical value |
|---|---|
| **macOS** (Homebrew) | `postgresql+asyncpg://YOUR_USERNAME@localhost:5432/smartlogistics` — Homebrew trusts local connections, so no password is needed |
| **Linux** | `postgresql+asyncpg://YOUR_USERNAME:smartlogistics@localhost:5432/smartlogistics` — the password you set with `ALTER ROLE` above |
| **Windows** | `postgresql+asyncpg://postgres:YOUR_PASSWORD@localhost:5432/smartlogistics` — the password the installer asked for |

Verified end to end on Ubuntu 24.04: these steps produce a working database, seeded data, and all
701 tests passing.

---

## Running

```bash
uvicorn app.main:app --reload --port 8000
```

Confirm it came up:

```bash
curl localhost:8000/health          # {"status":"ok", ...}
curl localhost:8000/health/ready    # 503 if PostgreSQL is unreachable
```

Interactive documentation is at **[/docs](http://localhost:8000/docs)** — see
[API reference](#api-reference) below.

### Trying it out

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@transfleet.com","password":"SmartLogistics!2026"}'
```

Use the returned `access_token` as `Authorization: Bearer <token>`.

**Security behaviour**

- Access tokens last 15 minutes; refresh tokens 14 days and **rotate on every use**
- Replaying a rotated refresh token revokes every session for that user
- 5 failed logins lock the account for 15 minutes
- Changing a password, or suspending an account, revokes all its sessions
- Admins cannot change their own role or status, or delete themselves

---

## API reference

**42 endpoints** across Authentication, Users, Warehouses, Shipments, Inventory and Health.
They are not listed here on purpose — a hand-written table drifts the moment a route changes.
Every reference below is generated from the running application, so none of them can:

| Where | Best for |
|---|---|
| **[/docs](http://localhost:8000/docs)** | Reading and calling endpoints in the browser. Click **Authorize** to paste a token |
| **[/redoc](http://localhost:8000/redoc)** | A cleaner read-only reference |
| **[docs/curl/index.html](docs/curl/index.html)** | Browsing offline or on GitHub — every endpoint with a copy-ready curl, live variable substitution, and a copy button that pastes into Postman's **Import → Raw text** |
| **[docs/curl/](docs/curl/)** | Postman collection + environment, per-tag shell scripts, and the raw OpenAPI spec |

Regenerate all of it after any route change:

```bash
make api
```

**Postman:** import the collection and environment from `docs/curl/`, select
**SmartLogistics — Local**, and run the login request — it stores the tokens automatically, so
every other request is authorised.

**Shell** (bash or zsh — on Windows use WSL2, Git Bash, or the HTML page above):

```bash
source docs/curl/env.sh     # logs in, exports BASE_URL and ACCESS_TOKEN
curl -s "$BASE_URL/api/v1/shipments" -H "Authorization: Bearer $ACCESS_TOKEN" | jq
```

Who may call what is documented in [phase_1.md §7b](docs/phase_1.md); the roles themselves are in
[ARCHITECTURE.md ADR-009](docs/ARCHITECTURE.md).

---

## Development data

`python -m app.seeders.runner` creates 11 accounts, all sharing the password in
`SEED_DEFAULT_PASSWORD` (`SmartLogistics!2026` by default).

| Email | Role |
|---|---|
| `admin@transfleet.com` | admin |
| `support.lead@transfleet.com` | customer_support |
| `support.agent@transfleet.com` | customer_support |
| `wh.karachi@transfleet.com` | warehouse_operator — KHI-01 |
| `wh.lahore@transfleet.com` | warehouse_operator — LHE-01 |
| `wh.regional@transfleet.com` | warehouse_operator — KHI-01, LHE-01, ISB-01 |
| `courier.one@transfleet.com` … `courier.four@transfleet.com` | courier |
| `courier.suspended@transfleet.com` | courier (suspended, for status testing) |

Seeders are idempotent — re-running creates nothing new — and refuse to run when
`ENVIRONMENT=production`.

```bash
python -m app.seeders.runner              # everything
python -m app.seeders.runner --only users # one seeder
```

---

## Database migrations

```bash
alembic upgrade head                          # apply everything
alembic revision --autogenerate -m "message"  # generate from model changes
alembic downgrade -1                          # roll back one revision
alembic current                               # show applied revision
```

Autogenerate does not emit `CREATE SCHEMA`. When a migration introduces a new module
schema, add `op.execute("CREATE SCHEMA IF NOT EXISTS <name>")` at the top of `upgrade()`
and register the schema in `VERSIONED_SCHEMAS` in `migrations/env.py`.

---

## Everyday commands

`make help` lists everything. The common ones:

| Command | What it does |
|---|---|
| `make install` | Create the venv and install dependencies |
| `make run` | Start the API with reload on :8000 |
| `make routes` | Print every registered route |
| `make migrate` | Apply migrations |
| `make migration m="add shipments"` | Generate a migration from model changes |
| `make seed` | Load development data |
| `make reset` | Drop, re-migrate and re-seed |
| `make api` | Regenerate `docs/curl/` — run after any route change |
| `make check` | Lint, format check and migration drift check |
| `make test` | Run the whole test suite |
| `make test-unit` | Unit tests only — no database needed |
| `make test-integration` | Services and repositories against Postgres |
| `make test-e2e` | HTTP tests through the running app |
| `make test-fast` | Skip the tests that open extra database connections |
| `make coverage` | Test suite plus a coverage report |
| `make docker-up` / `docker-down` | Start / stop the whole stack in containers |
| `make docker-seed` | Load development data into the containerised database |
| `make docker-test` | Run the test suite inside a container |
| `make docker-build` | Build the production image |
| `make k8s-build CLOUD=aws` | Render the Kubernetes manifests without applying |
| `make k8s-deploy CLOUD=aws` | Apply them (`CLOUD=azure` for AKS) |
| `make k8s-migrate CLOUD=aws` | Run the migration Job and wait for it |

### On native Windows

`make` is not available and the `Makefile` assumes `.venv/bin/` paths, so run the underlying
commands directly. The `docker-*` targets are the exception — `docker compose` works natively.

| Instead of | Run (PowerShell) |
|---|---|
| `make run` | `.venv\Scripts\uvicorn app.main:app --reload --port 8000` |
| `make migrate` | `.venv\Scripts\alembic upgrade head` |
| `make seed` | `.venv\Scripts\python -m app.seeders.runner` |
| `make test` | `.venv\Scripts\pytest` |
| `make api` | `.venv\Scripts\python scripts/generate_api_collection.py` |
| `make check` | `.venv\Scripts\ruff check src tests scripts` |
| `make docker-up` | `docker compose up -d --build` |

WSL2 avoids all of this — `make` works there exactly as on Linux.

## Automated tests

705 tests across three layers, at 92% line coverage.

```bash
make test              # or: .venv/bin/pytest   —   Windows: .venv\Scripts\pytest
```

They run against **real PostgreSQL**, not SQLite — the code depends on generated columns, native
enums, schema-qualified tables, sequences and row locking. The suite drops and rebuilds a separate
`smartlogistics_test` database by running the migrations from empty, so your development data is
never touched. Nothing else needs setting up: point `DATABASE_URL` at a running Postgres and the
suite creates what it needs.

| Layer | Tests | Database |
|---|---|---|
| `tests/unit` | 257 | No — runs anywhere, in under a second |
| `tests/integration` | 289 | Yes |
| `tests/e2e` | 139 | Yes |

Each test runs inside a transaction that is rolled back afterwards, so tests never interfere with
one another. The concurrency tests in `tests/integration/test_concurrency.py` are the exception —
row locking only exists between real connections — and are marked `slow`.

See [phase_1.md §10b](docs/phase_1.md) for what the suite covers and why.

## Project structure

```
Dockerfile          multi-stage; the runtime target is non-root
docker-compose.yml  Postgres, Redis, migrations, API
src/app/
  constants/        system-defined enums, patterns and defaults — the single source of truth
  controllers/      HTTP routes, dependencies, RBAC gates
  services/         business logic, transaction boundaries
  repositories/     database access
  models/           SQLAlchemy models — one file per entity
  schemas/          Pydantic request/response contracts
  seeders/          development data — one seeder per entity
  core/             config, database, security, tokens, exceptions
  main.py           application factory
migrations/         Alembic — 9 revisions
tests/              unit (277), integration (293), e2e (139)
deploy/
  docker/           container entrypoint
  k8s/              base manifests + aws / azure overlays
docs/               architecture, database reference, build log, briefs
scripts/            API collection generator
```

**Conventions**

- One file per entity, named after the singular table — `models/refresh_token.py`
- Every model must be imported in `models/__init__.py`, or Alembic will not see it
- One Postgres schema per module. Cross-schema foreign keys **are** used — the original rule
  against them was reversed once an audit found 15 unprotected references (ADR-008, amended)
- Repositories never commit; services own the transaction boundary
- Nothing user-facing is hard-deleted — soft delete with `deleted_at` (ADR-016)
- Statuses, roles and category values live in `constants/enums.py`, not scattered across the
  models and schemas that use them — see [phase_1.md §3](docs/phase_1.md)

---

## Documentation

| Document | Contents |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | **Start here.** System design, module boundaries, data and event flows, 16 ADRs, requirement traceability (§1.1) and NFR targets |
| [docs/database.md](docs/database.md) | Every schema, table, column and constraint, what each table is for, plus two class diagrams |
| [docs/phase_1.md](docs/phase_1.md) | Week 1 build log — what was built and why, access-control model, stock contention, defects found and fixed, what remains |
| [deploy/README.md](deploy/README.md) | The image, the Compose stack, and deploying to EKS or AKS |
| [docs/curl/](docs/curl/) | Generated API collection — OpenAPI spec, Postman files, curl scripts, and the browsable HTML page |
| [docs/projectDocs/](docs/projectDocs/) | The original assignment briefs (Guidelines, Part A, Part B) |

---

## Roadmap

| Week | Scope |
|---|---|
| **1** | Foundation, data model, auth, shipment and warehouse APIs, Docker setup |
| 2 | Dispatch workflow on Temporal, courier assignment, tracking state machine |
| 3 | Kafka events, Celery workers, notifications, analytics, observability |
| 4 | Data chunking, embeddings, vector store, semantic retrieval |
| 5 | AI assistant, delay insights, courier recommendations, streaming |
