# Phase 1 — Foundation & Core Services

**Milestone:** Week 1 of 5 · Part A — Core Platform
**Status:** In progress — identity and account management complete
**Last updated:** 2026-09-06

---

## 1. Scope

Week 1 of the execution plan calls for:

| Required | Status |
|---|---|
| Initial architecture and project setup | ✅ Done |
| User roles and auth basics | ✅ Done |
| Database schema design | ✅ Identity, warehouse, catalog, inventory and shipment modules complete |
| Shipment management APIs | ✅ Shipment and warehouse controllers complete |
| Local environment setup | 🟡 Postgres, MongoDB and Redis running locally; Docker Compose pending |
| Clean service structure established | ✅ Done |
| Docker setup operational | ✅ Done — image, Compose stack and Kubernetes manifests |

**At a glance:** 18 tables across 7 schemas, 42 endpoints, 9 migrations, 29 foreign keys,
81 application files, and 705 automated tests at 92% line coverage. Runs in Docker; deploys to
Kubernetes on EKS or AKS.

This document records what exists today, why it was built that way, and what remains.
Design rationale lives in [ARCHITECTURE.md](ARCHITECTURE.md); this is the build log.

---

## 2. Environment

Running locally rather than in containers for Week 1, to keep the iteration loop fast.
Docker Compose lands before the milestone closes.

| Component | Version | How it runs |
|---|---|---|
| Python | 3.13.13 | `uv` virtual environment at `.venv/` |
| PostgreSQL | 16.15 | Homebrew service, database `smartlogistics` |
| MongoDB | 7.x | Local instance, connected by URI (not containerised) |
| Redis | 7.x | Homebrew service |
| FastAPI | 0.141.1 | |
| SQLAlchemy | 2.0.52 | Async, `asyncpg` driver |
| Pydantic | 2.13.5 | |
| Alembic | 1.19.2 | |

MongoDB and Redis are provisioned and configured but **not yet used by application code** —
they come into play with tracking events (Week 2) and caching (Week 3).

**Setup issue worth recording:** `brew services start postgresql@16` failed with
`Bootstrap failed: 5: Input/output error`. The formula installs the binaries but does not
create the data directory. Resolved with `initdb --locale=C -E UTF-8 /opt/homebrew/var/postgresql@16`.

---

## 3. Project structure

Layer-first, one file per entity. Folders appear in the week their concern is built, so the
tree always reflects what actually exists — deferred folders are listed in
[ARCHITECTURE.md §16.2](ARCHITECTURE.md).

```
src/app/
├── constants/       system-defined enums, patterns, defaults — one source of truth
├── controllers/     HTTP routes, dependencies, RBAC gates
├── services/        business logic, transaction boundaries
├── repositories/    database access
├── models/          SQLAlchemy models — one file per entity
├── schemas/         Pydantic request/response contracts
├── seeders/         development data — one seeder per entity
├── core/            config, database, security, tokens, exceptions
└── main.py          application factory

tests/               unit (277), integration (293), e2e (139)
migrations/          9 Alembic revisions
deploy/
├── docker/          container entrypoint
└── k8s/             base manifests + aws / azure overlays
scripts/             API collection generator
```

Packaging lives at the root: `Dockerfile`, `docker-compose.yml` and
`docker-compose.override.yml`. See [§10c](#10c-packaging-and-deployment).

**81 application files in `src/`** (plus 2 in `scripts/` and 36 under `tests/`), across:

| Layer | Modules |
|---|---|
| `controllers` | auth, users, warehouses, shipments, inventory, health, plus shared `dependencies` |
| `services` | auth, user, warehouse, shipment, inventory, idempotency |
| `repositories` | base, user, refresh token, warehouse, shipment, courier |
| `models` | 18 tables across 7 schemas |
| `schemas` | user, auth, warehouse, zone, operating hours, sku, inventory, address, package, shipment, courier, assignment, common |
| `seeders` | warehouse, sku, user, inventory, courier, shipment |
| `core` | config, database, security, tokens, exceptions, access, db_errors, shipment_state_machine |
| `constants` | `enums` (statuses, roles, categories), `formats` (patterns, defaults, naming templates), `database` (schema names) |

### System-defined constants

Enums, patterns and defaults live in `constants/`, not scattered across the files that use them:

- **`constants/enums.py`** — every status, role and category value. Most back a real Postgres
  enum type; `DB_BACKED_ENUMS` names which ones and where, and
  `tests/integration/test_enum_constants.py` queries the live database to prove no enum has
  drifted from its Postgres type in either direction. Without this, the only way to learn what
  values a column accepts was to read a migration.
- **`constants/formats.py`** — validation patterns and business defaults that used to be
  hand-copied literals. The phone-number pattern alone was duplicated across five schema files;
  `DEFAULT_COUNTRY_CODE`, `DEFAULT_CURRENCY` and `DEFAULT_TIMEZONE` each existed in both a
  Pydantic default and a SQLAlchemy `server_default`, one edit away from disagreeing with itself.
- **`constants/database.py`** — the seven schema names, moved out of `models/base.py` so
  `constants` has no dependency on `models` and the two packages can never form an import cycle.

### Conventions established

- One file per entity, named after the singular table — `models/refresh_token.py`
- `models/__init__.py` is a registry; every model is imported there, or Alembic will not see it
  and SQLAlchemy cannot resolve string relationships
- Cross-file relationships use `TYPE_CHECKING` imports with string targets, avoiding import cycles
- Repositories never commit — services own the transaction boundary so one request can span
  several repositories atomically
- Services raise domain exceptions; a single handler in `main.py` maps them to HTTP responses,
  so no layer below `controllers` knows about status codes
- One Postgres schema per module. Cross-schema foreign keys **are** used — the original rule
  forbidding them was reversed once an audit found 15 unprotected references, and ADR-008 records
  the amendment. See [§7d](#7d-referential-integrity-and-deletion)

---

## 4. Data model — `identity` schema

> Column-by-column detail for every table, plus class diagrams, lives in
> [database.md](database.md). This section covers design intent.

Four tables, all created by migration `c4014b28861d`.

| Table | Purpose |
|---|---|
| `users` | Accounts, roles, credentials, lockout state |
| `refresh_tokens` | Refresh-token sessions with rotation tracking |
| `password_reset_tokens` | Single-use, hashed, short-lived reset tokens |
| `user_warehouse_assignments` | Scopes a warehouse operator to specific warehouses |

### `users`

| Column | Notes |
|---|---|
| `id` | UUID primary key, opaque and non-enumerable |
| `email` | Unique, lowercased at the schema boundary |
| `full_name`, `phone` | Profile |
| `password_hash` | Argon2id |
| `password_changed_at` | Supports future "password age" policies |
| `role` | Enum: `admin`, `customer_support`, `warehouse_operator`, `courier` |
| `status` | Enum: `pending_activation`, `active`, `suspended`, `deactivated` |
| `failed_login_attempts`, `locked_until` | Brute-force lockout state |
| `last_login_at` | |
| `created_at`, `updated_at` | Timestamp mixin |

Indexes: unique on `email`, composite on `(role, status)` for filtered listings.

### `refresh_tokens`

Only the **SHA-256 hash** of the token is stored, so a database leak cannot be replayed as a
login. `replaced_by_id` records rotation: presenting an already-rotated token is treated as
theft and revokes the entire session chain.

### `user_warehouse_assignments`

Backs the row-level scoping rule in ADR-009 — the role grants the endpoint, this table grants
the rows. `warehouse_id` deliberately has **no foreign key**, since warehouses will live in a
different schema.

---

## 4b. Data model — `warehouses` schema

Three tables, created by migration `29fcffc781d1`.

| Table | Purpose |
|---|---|
| `warehouses` | Facilities: fulfilment centres, hubs, depots, returns centres |
| `warehouse_zones` | Functional areas within a facility, following goods flow |
| `warehouse_operating_hours` | Weekly opening windows, one row per weekday |

### `warehouses`

| Column | Notes |
|---|---|
| `code` | Unique, uppercase, e.g. `KHI-01`. The human-facing identifier used on labels |
| `name` | |
| `type` | `fulfillment_center`, `distribution_hub`, `regional_depot`, `returns_center` |
| `status` | `active`, `maintenance`, `inactive` — only `active` may dispatch |
| `address_line1/2`, `city`, `region`, `postal_code`, `country_code` | Inline address |
| `latitude`, `longitude` | Facility centroid — routing and distance maths |
| `entrance_latitude`, `entrance_longitude` | The gate a courier is routed to; can be hundreds of metres from the centroid |
| `map_place_id` | Provider-stable reference (Google `place_id`, OSM node) that survives address edits |
| `geofence_radius_m` | Radius counting as "at the warehouse"; arrival is derived from pings, not self-reported |
| `geocoded_at` | Coordinate freshness, so stale geocodes can be refreshed in bulk |
| `capacity_units` | Total storage units |
| `max_daily_outbound` | Throughput ceiling, used by dispatch planning |
| `timezone` | IANA name; operating hours are local to it |
| `contact_name/email/phone`, `notes` | |

Indexes: unique on `code`, plus `(city, status)` and `(type, status)` for filtered listings.
Check constraints: positive capacity, positive geofence radius, and latitude/longitude range
checks on both the centroid and the entrance.

**Live tracking readiness (ADR-015).** The model is built for a map provider — Google Maps or
an open OSM/OSRM stack — without committing to one. Coordinates stay plain columns rather than
PostGIS until a query needs spatial indexing; high-frequency courier position pings will go to
MongoDB with a `2dsphere` index and a TTL, never to Postgres; and the provider sits behind a
`MapProvider` adapter so dispatch never blocks on a third-party call.

**Why the address is inline rather than a foreign key to a shared `addresses` table:**
a warehouse has exactly one permanent address, so a join buys nothing, and the shared address
table will live in the `shipments` schema — which ADR-008 forbids referencing across.

### `warehouse_zones`

Zones give inventory a location more precise than the facility, which is what makes picking
and staging reportable once inventory exists. `code` is unique **within** a warehouse, not
globally, since operators read codes off physical signage.

Eight zone types follow the flow of goods: `receiving`, `storage`, `picking`, `packing`,
`staging`, `dispatch`, `returns`, `quarantine`.

### `warehouse_operating_hours`

One row per weekday, ISO-8601 numbering (Monday = 1). Times are local to the warehouse's
`timezone`. Dispatch scheduling will use these windows to decide whether a facility can
release a shipment, and courier pickup slots will be validated against them.

A database check constraint enforces the only two valid shapes: a closed day with no times,
or an open day where `closes_at > opens_at`. The same rule is mirrored in the Pydantic schema
so bad input fails at the API boundary rather than in the database.

**Weekly schedules are replaced wholesale, never patched.** A partial update is ambiguous —
an absent day could mean "unchanged" or "closed" — so `WeeklyScheduleUpdate` always carries
the full week.

### Verified constraints

| Attempt | Result |
|---|---|
| Closed day carrying opening times | Rejected — `valid_window` |
| `closes_at` earlier than `opens_at` | Rejected — `valid_window` |
| Two rows for the same weekday | Rejected — `uq_warehouse_operating_day` |
| Duplicate zone code within one warehouse | Rejected — `uq_warehouse_zone_code` |
| Zone with zero capacity | Rejected — `capacity_positive` |
| Deleting a warehouse | Zones and hours cascade; no orphans |

---

## 4c. Data model — `catalog`, `inventory` and `shipments`

Eight tables across three schemas, created by migration `cd149d065b36`.

### `catalog.skus`

Deliberately thin: SmartLogistics moves goods, it does not merchandise them. Only attributes
affecting packing, courier capacity or handling are modelled — weight, dimensions, and the
`is_fragile` / `is_hazmat` / `requires_cold_chain` flags that will constrain zone and courier
selection. Pricing and marketing data stay in the upstream commerce system.

Weights are **grams** and dimensions **millimetres**, as integers. Summing hundreds of items
into a shipment weight with floats would drift; integers cannot.

### `inventory.inventory_items`

One row per SKU per warehouse — the consistency-critical table (driver D2).

| Column | Notes |
|---|---|
| `on_hand_qty`, `reserved_qty` | The two authoritative counters |
| `available_qty` | **Postgres generated column**, `on_hand_qty - reserved_qty`, stored |
| `reorder_level` | Threshold for restock alerts |
| `version` | Optimistic-concurrency counter for non-locking readers |

Three check constraints make overselling impossible **at the storage layer**, independent of
any application bug above it: `on_hand_qty >= 0`, `reserved_qty >= 0`, and
`reserved_qty <= on_hand_qty`. Because `available_qty` is generated, it can never disagree with
its inputs — the database rejects any attempt to write it directly.

### `inventory.inventory_reservations`

A hold on stock for a specific shipment, with `expires_at`. If a dispatch workflow dies in a way
Temporal cannot compensate, a scheduled reaper releases the hold — stock can never be
permanently stranded, which is the failure mode most likely under load and hardest to notice.
`idempotency_key` is unique, making `reserve_inventory` safe for Temporal to retry.

### `inventory.stock_movements`

An **immutable ledger**: one append-only row per stock change, with a signed `quantity_delta`
and the `resulting_on_hand` after it. Levels answer "how much is there now"; the ledger answers
"how did it get there", which is what makes a discrepancy investigable. Rows are never updated
or deleted, so there is no `updated_at`.

### `shipments.shipments`

The aggregate root. Cross-schema references (`origin_warehouse_id`, `courier_id`, `created_by`)
carry no foreign key per ADR-008; `destination_address_id` does, being in the same schema.

Lifecycle timestamps (`dispatched_at`, `picked_up_at`, `delivered_at`, `cancelled_at`) are each
set once, by the transition that earns them. `version` guards every status change with
optimistic concurrency. `dispatch_workflow_id` links the row to its Temporal workflow for
diagnosis.

### `shipments.addresses`

Stored **per shipment, not per customer**. An address is a snapshot of where a parcel was
actually sent — editing a customer's saved address must never rewrite the destination of a
parcel already delivered. Carries coordinates and `map_place_id` for routing and geofenced
delivery confirmation (ADR-015).

### `shipments.shipment_items`

SKU attributes are **snapshotted** at creation — `sku_code`, `sku_name`, `unit_weight_g`,
`unit_value`. If a product's weight is corrected next month, an already-shipped consignment must
still show what was actually sent; a live join would silently rewrite history.

### `shipments.packages`

Physical parcels; one shipment may split across several. The `barcode` is unique **system-wide**
rather than per shipment, because that is what a warehouse scanner and a courier's device
actually read.

### Shipment state machine

`core/shipment_state_machine.py` is the single declaration of legal transitions — 13 states,
with `delivered`, `returned` and `cancelled` terminal. A transition not listed there cannot
happen anywhere in the codebase; illegal attempts raise `ConflictError` → HTTP 409, never a
silent write.

Two derived sets support the workflow logic: `HOLDS_INVENTORY` (states where stock must be
released on cancellation) and `IN_COURIER_CUSTODY`.

### Verified constraints

| Attempt | Result |
|---|---|
| Reserve more stock than is on hand | Rejected — `reserved_within_on_hand` |
| Negative on-hand quantity | Rejected — `on_hand_non_negative` |
| Write directly to `available_qty` | Rejected — generated column |
| Change `reserved_qty` | `available_qty` recomputed automatically |
| Same SKU twice on one shipment | Rejected — `uq_shipment_item_sku` |
| Zero-quantity shipment line | Rejected — `quantity_positive` |
| Delete an address still referenced | Rejected — foreign key `RESTRICT` |
| Ledger sum vs. on-hand levels | 0 mismatches across all 30 positions |

---

## 4d. Data model — `couriers` schema

Two tables, created by migration `824ac2610207`.

### Why a courier is not just a user with `role = courier`

`identity.users` holds credentials and access. `couriers.couriers` holds fleet capability —
vehicle, capacity, availability, handling clearances, performance. Keeping them separate means
work is assigned to a *fleet profile*, not a login, which allows a partner courier to exist with
no account at all. `CR-0006` in the seed data is exactly that case.

### `couriers.couriers`

| Group | Columns | Purpose |
|---|---|---|
| Link | `user_id` (unique, nullable, no FK) | 1:1 with the login when there is one |
| Capability | `vehicle_type`, `capacity_kg`, `can_handle_hazmat` / `_cold_chain` / `_fragile` | Whether the shipment physically and legally fits. Mirrors the SKU handling flags |
| Availability | `availability_status`, `home_city`, `service_cities`, `base_warehouse_id` | Whether to offer work now, and where |
| Load | `max_daily_shipments`, `current_load` | Numerator and denominator of Courier Utilization Rate |
| Performance | `rating`, `total_deliveries`, `failed_deliveries` | Ranking inputs |
| Position | `last_latitude`, `last_longitude`, `last_location_at` | Denormalised from the MongoDB ping stream (ADR-015) |

`can_accept_work` on the model encapsulates the ranker's first filter: active, on shift, and
with remaining capacity.

### `couriers.courier_assignments`

One row per offer of one shipment to one courier. `shipments.courier_id` records who holds a
shipment **now**; this table records **how it got there**.

Three requirements make it necessary rather than nice to have:

1. The brief lists "courier assignment changes" as a domain event and calls for reassignment
   workflows — a single FK column has no history to emit.
2. **Courier Utilization Rate** and courier performance reporting need the record of every
   offer, acceptance and rejection, including `response_seconds`.
3. The dispatch saga's `assign_courier` compensation marks a row `reassigned` rather than
   silently nulling a column, so the reversal is auditable.

`sequence_no` orders attempts within a shipment; `reason` captures why a rejection or
reassignment happened, which feeds the Part B delay-insight engine.

### The relationship

```
identity.users ──1:1── couriers.couriers ──1:N── courier_assignments ──N:1── shipments.shipments
                                                                                    │
shipments.courier_id ──────────── denormalised pointer to the current assignee ──────┘
```

`shipments.courier_id` is kept **alongside** the assignment table on purpose: "my shipments
today" is the courier app's hottest query, and it should be one indexed lookup rather than a
join through assignment history. The cost is an invariant — the pointer must agree with the
current assignment — which the service layer enforces and a query verifies (0 mismatches).

### Correction made

The shipment seeder originally set `courier_id` to a **user** id. Work is assigned to a fleet
profile, so it now points at `couriers.id`. Row-scoping resolves user → courier once per request.

---

## 4e. Data model — `platform` schema

Cross-cutting infrastructure, owned by no domain module.

| Table | Purpose |
|---|---|
| `idempotency_keys` | Stored results of write requests, keyed by the client's `Idempotency-Key` |

The unique index on `key` doubles as concurrency control: two simultaneous requests with the
same key race to insert, and the loser is told the first is still in flight. See §7c.

The outbox and consumer dedupe tables described in ARCHITECTURE.md §6.1 also belong here; they
arrive with Kafka in Week 3.

---

## 5. Migrations

Alembic configured for async SQLAlchemy, reading `DATABASE_URL` from application settings.

```bash
make migrate                            # apply
make migration m="add shipments"        # generate from model changes
```

### Applied migrations

| # | Revision | Change |
|---|---|---|
| 1 | `c4014b28861d` | Create `identity` schema |
| 2 | `29fcffc781d1` | Create `warehouses` schema |
| 3 | `b37006804da9` | Warehouse geolocation and live-tracking fields |
| 4 | `cd149d065b36` | Create `catalog`, `inventory` and `shipments` schemas |
| 5 | `824ac2610207` | Create `couriers` schema |
| 6 | `bfaf0f1c3d6e` | Shipment reference sequence |
| 7 | `5bfaf91d847d` | Create `platform` schema with idempotency keys |
| 8 | `72f40f9e767d` | Cross-schema foreign keys and soft-delete columns |
| 9 | `550bd006b4c4` | Partial indexes over live rows |

Every migration has a working `downgrade`; migration 8 was downgraded and re-applied during
development to correct constraint names.

Two things autogenerate does not handle, both documented in the README:

1. **`CREATE SCHEMA` is not emitted.** New module schemas need
   `op.execute("CREATE SCHEMA IF NOT EXISTS <name>")` at the top of `upgrade()`.
2. **New schemas must be added to `VERSIONED_SCHEMAS`** in `migrations/env.py`, or autogenerate
   will not see their tables.

`make check` runs `alembic check`, which fails the build if models and migrations have drifted.

---

## 6. Development data

```bash
make seed                                  # all seeders
python -m app.seeders.runner --only users  # one seeder
```

11 accounts covering every role, all sharing `SEED_DEFAULT_PASSWORD`:

| Account | Role |
|---|---|
| `admin@transfleet.com` | admin |
| `support.lead@`, `support.agent@` | customer_support |
| `wh.karachi@` (KHI-01), `wh.lahore@` (LHE-01), `wh.regional@` (3 warehouses) | warehouse_operator |
| `courier.one@` … `courier.four@` | courier |
| `courier.suspended@` | courier, suspended — for status testing |

### Warehouses

5 facilities, one per type, with zones and a full weekly schedule each:

| Code | Type | City | Zones | Status |
|---|---|---|---|---|
| `KHI-01` | fulfillment_center | Karachi | 6 | active |
| `LHE-01` | distribution_hub | Lahore | 6 | active |
| `ISB-01` | regional_depot | Islamabad | 6 | active |
| `FSD-01` | returns_center | Faisalabad | 4 | active, closed weekends |
| `PEW-01` | regional_depot | Peshawar | 3 | **maintenance** — for status testing |

The warehouse seeder runs **before** the user seeder, and derives ids with
`seed_id("warehouse", code)` — the same expression the user seeder used for warehouse
assignments before any warehouse existed. Verified: the three operators seeded earlier now
join cleanly to real facilities.

### Catalog, inventory and shipments

- **10 SKUs** across electronics, homeware, apparel, books, plus one hazmat and one cold-chain
  item to exercise handling flags
- **30 stock positions** — 10 SKUs across KHI-01, LHE-01 and ISB-01, each with a matching
  opening ledger entry. The returns centre and the facility under maintenance carry no stock
- **6 couriers** — one per vehicle type, spanning available / on-duty / off-duty, one
  deactivated, and one partner courier with no login
- **5 courier assignments**, including a rejection followed by a reassignment on
  `SL-2026-000006`, so assignment history is exercised
- **7 shipments** spanning the lifecycle: `created`, `ready_for_dispatch`, `dispatched`,
  `out_for_delivery`, `delivered`, `delivery_failed` (2 attempts) and `cancelled`

**Three properties that matter:**

1. **Idempotent** — existing emails are skipped, so re-running creates nothing. No truncate,
   no destructive reset.
2. **Deterministic ids** via `uuid5`, so seeded rows keep the same id across runs and can be
   hardcoded in tests. It also lets seeders cross-reference before their dependencies exist:
   the operator's `warehouse_id` is `seed_id("warehouse", "KHI-01")`, which the future warehouse
   seeder will generate for the same code.
3. **Single transaction and a production guard** — a partial seed is worse than none, and
   `ENVIRONMENT=production` refuses outright.

---

## 7. API

42 endpoints. Interactive documentation at `/docs` (Swagger UI) and `/redoc`.

### Authentication — `/api/v1/auth`

| Method | Path | Access | Purpose |
|---|---|---|---|
| POST | `/login` | public | Returns access + refresh tokens and the profile |
| POST | `/refresh` | public | Rotates the refresh token |
| POST | `/logout` | authenticated | Revokes one session, or all if the token is omitted |
| GET | `/sessions` | authenticated | Lists the caller's active sessions |
| GET | `/me` | authenticated | Caller's profile |

### Users — `/api/v1/users`

| Method | Path | Access | Purpose |
|---|---|---|---|
| POST | `` | admin | Create an account |
| GET | `` | admin | List, filtered by `role`, `status`, `search`, paginated |
| GET | `/{id}` | admin | Fetch one |
| PATCH | `/me` | authenticated | Update own profile |
| POST | `/me/password` | authenticated | Change own password |
| PATCH | `/{id}` | admin | Update a profile |
| PATCH | `/{id}/role` | admin | Change role |
| PATCH | `/{id}/status` | admin | Suspend, reactivate, deactivate |
| DELETE | `/{id}` | admin | Soft delete |

### Warehouses — `/api/v1/warehouses`

| Method | Path | Access |
|---|---|---|
| GET | `/locations` | any authenticated — courier navigation view |
| GET | `` | admin, support: all · operator: own |
| GET | `/summary` | any authenticated — compact list |
| POST | `` | admin |
| GET | `/{id}` | admin, support · operator: own |
| PATCH | `/{id}` | admin |
| PATCH | `/{id}/status` | admin |
| DELETE | `/{id}` | admin (soft) |
| POST | `/{id}/zones` | admin · operator: own |
| PATCH, DELETE | `/zones/{zone_id}` | admin · operator: own |
| GET | `/{id}/operating-hours` | any authenticated in scope |
| PUT | `/{id}/operating-hours` | admin · operator: own |

### Shipments — `/api/v1/shipments`

| Method | Path | Access |
|---|---|---|
| POST | `` | admin, support |
| GET | `` | row-scoped per role |
| GET | `/{id}` | row-scoped per role |
| PATCH | `/{id}` | admin, support — pre-dispatch only |
| PATCH | `/{id}/status` | per-transition role map |
| POST | `/{id}/cancel` | admin, support |
| POST | `/{id}/packages` | admin · operator: own warehouse |
| PATCH | `/packages/{package_id}` | admin · operator: own warehouse |

### Inventory — `/api/v1/inventory`

| Method | Path | Access |
|---|---|---|
| GET | `` | admin, support: all · operator: own warehouses |
| GET | `/reservations` | admin, support: all · operator: own warehouses |
| POST | `/release-expired` | admin · operator: own warehouses |

### Health

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness |
| GET | `/health/ready` | Readiness — verifies PostgreSQL connectivity |

Probes sit outside the `/api/v1` prefix so orchestrators need not track API versions.

---

## 7b. Access control model

Authorisation is two-layered (ADR-009). The **role gate** decides whether an endpoint may be
called; **row scoping** decides which records the caller may see or touch. Role checks alone
would let a courier enumerate another courier's work.

`core/access.py` builds an `AccessScope` once per request: admins and support are unrestricted,
a warehouse operator carries their assigned warehouse ids, a courier carries their fleet
profile id.

### Warehouses

| Operation | admin | warehouse_operator | customer_support | courier |
|---|---|---|---|---|
| List / view | all | **own only** | all | active only, location view |
| Create, update, change status, deactivate | ✅ | ❌ | ❌ | ❌ |
| Manage zones | ✅ | **own only** | ❌ | ❌ |
| Manage operating hours | ✅ | **own only** | ❌ | ❌ |

Creating a facility or taking one offline is a network-level decision with dispatch
consequences, so it stays admin-only. Zones and opening hours are day-to-day operational
reality the operator on site knows best.

`GET /warehouses/locations` is the courier's navigation view: address, coordinates, entrance,
geofence, phone and opening hours — and nothing about capacity, throughput, contacts or
internal layout.

### Shipments

| Operation | admin | warehouse_operator | customer_support | courier |
|---|---|---|---|---|
| List / view | all | own warehouses | all | **own assignments only** |
| Create | ✅ | ❌ | ✅ | ❌ |
| Update (pre-dispatch) | ✅ | ❌ | ✅ | ❌ |
| Mark ready for dispatch | ✅ | **own warehouses** | ❌ | ❌ |
| Manage packages | ✅ | **own warehouses** | ❌ | ❌ |
| Cancel | ✅ | ❌ | ✅ | ❌ |
| Custody transitions | ✅ | ❌ | ❌ | **own only** |
| Initiate return | ✅ | ❌ | ✅ | ❌ |

Each role owns the part of the lifecycle it can actually observe: the warehouse marks a
shipment ready because it packed it, the courier drives the custody states because they are
holding the parcel, support initiates returns because the customer called them.

### Denial semantics

A caller who fails **row scoping** gets **404, not 403**. Whether another courier's shipment
exists is itself information they are not entitled to. A caller who fails the **role gate**
gets 403, because the endpoint's existence is not sensitive.

### Editing window

Shipments are editable only in `created`, `ready_for_dispatch` or `dispatch_failed`. Changing
contents after dispatch would misrepresent what was actually sent.

---

## 7c. Stock contention and idempotency

Three questions had to be answered before shipment CRUD could be trusted: who gets the stock
when two requests want it, what happens to stock held for an order that is never confirmed, and
what happens when a client retries a request it never saw the answer to.

### Who wins the stock

**The first request to acquire the row lock.** Reserving takes
`SELECT ... FOR UPDATE` on the affected `inventory_items` rows, so concurrent requests
serialise rather than both reading the same stale availability. The loser is refused
immediately with `insufficient_stock` and the quantities that were actually available.

Rows are locked **in id order**, so two multi-line requests sharing SKUs cannot deadlock by
taking the same locks in opposite sequences.

The hold is **all-or-nothing**: a partially reserved shipment cannot be dispatched anyway, so a
shortfall on any line refuses the whole request.

### When stock is held

**At creation, not at dispatch.** This changed during the phase. Reserving only inside the
Phase 2 dispatch saga would mean two orders could both be accepted for the same units and one
discovers the shortfall much later — exactly the "inventory inconsistency" the brief complains
about. Holding at creation makes the refusal immediate and honest.

The hold is taken in the **same transaction** that creates the shipment, so a shipment can never
exist without its stock, nor stock be held without a shipment.

| Event | Effect on stock |
|---|---|
| Shipment created | `reserved_qty += quantity`, reservation `held` with `expires_at` |
| Shipment cancelled (before dispatch) | `reserved_qty -= quantity`, reservation `released` |
| Shipment dispatched | `on_hand_qty -= quantity` **and** `reserved_qty -= quantity`, reservation `committed`, ledger entry written |
| Hold expires | `reserved_qty -= quantity`, reservation `released` with reason "Hold expired" |

### Unconfirmed orders

Holds carry `expires_at`, set from `INVENTORY_HOLD_MINUTES` (default 120). Stock reserved for an
order nobody confirms is reclaimable.

**Expiry is lazy, not scheduled.** When a request locks a set of stock rows, it first releases
any expired holds *on those rows*, inside the same transaction, then measures availability. This
means correctness does not depend on a cron job running — contended stock is always freed at the
moment somebody actually wants it. `POST /inventory/release-expired` sweeps the rest on demand;
a Celery beat schedule replaces the manual call in Phase 3.

### Retries

`POST /api/v1/shipments` accepts an optional **`Idempotency-Key`** header. The first request
records the key with a hash of its body; a repeat returns the stored response instead of
creating a second shipment.

| Case | Behaviour |
|---|---|
| Same key, same body, first request finished | Returns the original response, HTTP 200 |
| Same key, **different** body | **422** `idempotency_key_reuse` — silently returning the old response would hide a client bug |
| Same key, different endpoint | 422 |
| Same key, original still running | **409** `request_in_progress` — the unique index on `key` is the concurrency control |
| Original request failed | Key is discarded, so the client may correct and retry |
| No key supplied | No protection; duplicates are the client's responsibility |

Keys expire after `IDEMPOTENCY_TTL_HOURS` (default 24).

### Verified

| Scenario | Result |
|---|---|
| 10 units available; request A takes 7 | Accepted, 3 available |
| Request B wants 7 | **409** `Insufficient stock for: SKU-BOOK-0001 (requested 7, available 3)` |
| Request C takes the remaining 3 | Accepted, 0 available |
| Request D wants 1 | 409 |
| Cancel C | 3 units returned immediately |
| Request D retried for 3 | Accepted |
| POST twice with the same Idempotency-Key | Same reference number, **1** row in the database |
| Same key with a changed quantity | 422 |
| Failed request's key | Deleted, retry allowed |
| Holds expired, new request arrives | Both stale holds released automatically, new hold placed |
| Shipment dispatched | On-hand fell by 9, hold cleared, `outbound_dispatch` ledger entry written |
| Ledger vs. stock levels after all of it | 0 mismatches |

### Deliberately deferred

Scheduled reaping (Celery beat), reservation events on Kafka, and the dispatch saga taking over
commit and compensation all arrive with their own weeks. The behaviour above needs none of them
to be correct today.

---

## 7d. Referential integrity and deletion

### Foreign keys, not hand-written checks

ADR-008 originally forbade cross-schema foreign keys so a module could be extracted cleanly.
An audit found **15 unprotected references** — nothing stopped a shipment pointing at a
warehouse that did not exist, and integrity depended on the service layer remembering to check.

The rule is amended: foreign keys are now declared wherever the relationship is permanent.
**29 foreign keys**, up from 11.

| Delete rule | Applied to | Why |
|---|---|---|
| `RESTRICT` | stock → SKU / warehouse, shipment → warehouse / courier, shipment item → SKU, reservation → shipment | The parent must not vanish while records depend on it |
| `SET NULL` | shipment → creator, ledger → actor, courier → user account, stock → zone | The record outlives the account or placement behind it |
| `CASCADE` | zones, hours, shipment items, packages, refresh tokens, warehouse assignments | A child has no meaning without its parent |

### Errors handled, not pre-empted

Pre-checks like `email_exists()` and `code_exists()` are gone. They duplicated a rule the
database already enforces, and a check-then-insert is a race in any case.

`core/db_errors.py` now translates `IntegrityError` into domain errors by SQLSTATE, using the
constraint name to pick a specific message. Constraint names are stable because
`models/base.py` fixes them with a naming convention.

| SQLSTATE | Meaning | Response |
|---|---|---|
| `23505` | Unique violation | 409 `conflict` |
| `23503` | Foreign key violation | 409 `invalid_reference` |
| `23514` / `23502` | Check or not-null violation | 422 `constraint_violation` |

Verified — messages are as specific as the hand-written ones were:

| Attempt | Response |
|---|---|
| Duplicate warehouse code | 409 "A warehouse with this code already exists." |
| Duplicate user email | 409 "A user with this email already exists." |
| Duplicate zone code in one warehouse | 409 "This zone code is already used in this warehouse." |
| Hard-deleting a warehouse holding stock | Rejected by `fk_inventory_items_warehouse_id_warehouses` |
| Hard-deleting a SKU with stock | Rejected by `fk_inventory_items_sku_id_skus` |
| Inserting stock for a non-existent warehouse | Rejected by the same constraint |

### Soft deletion

`SoftDeleteMixin` adds `deleted_at` and `deleted_by` to users, warehouses, zones, SKUs,
couriers and shipments. Nothing user-facing is hard-deleted.

- `DELETE` marks the row and returns 204; the row stays in the table
- Repositories exclude soft-deleted rows by default — `BaseRepository.active_only`
- `POST /{id}/restore` brings it back, which a hard delete could never do
- Deletion **also flips domain state**: a deleted warehouse becomes `inactive`, a deleted user
  `deactivated` with every session revoked, so it stops participating in business logic
  immediately rather than merely disappearing from lists

Verified: deleting a warehouse returned 204, the row remained with `deleted_at` set, `GET`
returned 404, it vanished from listings, and restore brought it back `active`. A deleted user
could not log in (`account_inactive`) and could log in again after restore.

### Table growth

**Partial indexes rather than archive tables.** Six indexes carry `WHERE deleted_at IS NULL`,
so they stay the size of the live data no matter how much history accumulates. They are declared
on the models, so `alembic check` keeps model and database in agreement.

Archival to an `archive` schema, driven by a scheduled job with a retention window, is an
additive step for when volume justifies it — nothing in the application layer changes when it
happens.

**One consequence worth knowing:** unique constraints still count deleted rows, so a
soft-deleted warehouse keeps its code reserved. That is deliberate — restoring it must not
collide with a replacement created in the meantime.

### Which tables carry soft deletion, and which do not

**6 of 18 tables have `deleted_at`.** The rule: soft deletion goes on independently deletable
top-level entities — the things a user clicks "delete" on and might regret.

| Has `deleted_at` | |
|---|---|
| `identity.users`, `warehouses.warehouses`, `warehouses.warehouse_zones`, `catalog.skus`, `couriers.couriers`, `shipments.shipments` | Deletable in their own right, and referenced by other rows that must keep resolving |

The remaining twelve fall into four groups, each with a different reason:

| Group | Tables | Why not |
|---|---|---|
| **Immutable ledger and history** | `inventory.stock_movements`, `couriers.courier_assignments` | Append-only by design. They are never deleted, so there is nothing to soften — and a deletable ledger is not a ledger |
| **Status lifecycle instead** | `inventory.inventory_reservations` (`held → committed / released`), `identity.refresh_tokens` (`revoked_at`), `identity.password_reset_tokens` (`used_at`) | Already carry a "no longer active" state; a second one would be ambiguous |
| **Children that cascade** | `shipments.shipment_items`, `shipments.packages`, `shipments.addresses`, `warehouses.warehouse_operating_hours` | No meaning without their parent, and removed only when it is |
| **Ephemeral** | `platform.idempotency_keys` | TTL-expired and genuinely disposable |

`inventory.inventory_items` is never deleted at all: a stock position at zero is still a real
position, and deleting it would lose the reorder threshold and zone placement.

`identity.user_warehouse_assignments` cascades from both sides — it is a link row, not an
entity.

### Known gaps in the rule

Three places currently bend it. None is load-bearing yet, all are worth closing before the
delete surface grows:

| # | Gap | Consequence |
|---|---|---|
| 1 | `PUT /warehouses/{id}/operating-hours` **hard-deletes** the week before re-inserting it | Defensible as a value-collection replace, but it is a permanent delete and the audit trail of a schedule change is lost |
| 2 | Revoking a warehouse operator's assignment will be a hard delete when that endpoint is added | Loses the record of who previously had access — an audit gap |
| 3 | `packages` have no delete endpoint yet | When one is added it should be soft: a barcode may already be printed and scanned |

The rule also lives only in this document and ADR-016 — nothing in the code stops a future
service calling `session.delete()`. A test asserting that no service outside an allowlist does
so would make the rule enforceable rather than merely documented.

---

## 8. Security implementation

| Control | Implementation |
|---|---|
| Password hashing | Argon2id via `argon2-cffi` |
| Access tokens | JWT HS256, 15 minutes, carrying `sub`, `role`, `type`, `jti` |
| Refresh tokens | Opaque random strings, 14 days, stored hashed — therefore revocable |
| Token rotation | Every refresh issues a new pair and retires the old token |
| Theft detection | Replaying a rotated token revokes **every** session for that user |
| Type confusion | A refresh token is rejected if presented as a bearer credential |
| Brute force | 5 failed logins lock the account for 15 minutes |
| Account enumeration | Unknown emails still run a dummy hash, keeping response timing constant |
| Immediate revocation | Suspension and password change revoke all sessions |
| Privilege escalation | Admins cannot change their own role or status, or delete themselves |
| Authorization | Role gate per endpoint, plus row-level scoping in repositories |

### Why authorization re-reads the database

`get_current_user` loads the user on every request and checks `user.role`, rather than trusting
the token's `role` claim. If RBAC trusted the claim, demoting an admin would leave them admin
until their token expired. The claim is still present for clients and for future async consumers,
but the database is authoritative. Verified: suspending a user invalidates their live token
on the next request.

### Error model

All non-2xx responses share one shape:

```json
{ "code": "conflict", "message": "A user with this email already exists." }
```

Validation failures add a `detail` array of `{field, error}`. Domain exceptions carry their own
status code and stable `code`, translated in a single handler in `main.py`.

---

## 9. Tooling

| Command | Purpose |
|---|---|
| `make install` | Create venv, install dependencies |
| `make run` | Start API with reload |
| `make routes` | Print every registered route |
| `make migrate` / `make migration m="…"` | Migrations |
| `make seed` / `make reset` | Development data |
| `make api` | Regenerate `docs/curl/` |
| `make check` | Lint, format check, migration drift check |
| `make test` | Whole test suite |
| `make test-unit` / `test-integration` / `test-e2e` | One layer at a time |
| `make test-fast` | Skips the tests that open extra database connections |
| `make coverage` | Suite plus a coverage report |

`uv` for dependencies, `Ruff` for lint and format (line length 100, rule set
`E,F,I,N,UP,B,SIM,RUF`). Lint is currently clean.

### API collection

`docs/curl/` is generated from the live routes by `scripts/generate_api_collection.py`, so it cannot
drift from the code:

- `SmartLogistics.postman_collection.json` — 16 requests in 3 folders; the login request stores
  tokens into the Postman environment automatically
- `SmartLogistics.postman_environment.json`
- `index.html` — browsable reference: every endpoint with a copy-ready curl command, live
  substitution of base URL, token and path parameters, and per-command copy buttons
- `openapi.json` — importable into any API client
- `env.sh` plus one `.sh` per tag, holding runnable curl commands

Regenerate with `make api` after any route change.

---

## 10. Verification

Twenty scenarios executed against the live API and the local database. All passed.

| # | Scenario | Result |
|---|---|---|
| 1 | Login with valid credentials | 200, token pair and profile |
| 2 | Wrong password | 401 `authentication_failed` |
| 3 | Suspended account | 401 `account_inactive` |
| 4 | `GET /auth/me` | Profile with `last_login_at` populated |
| 5 | Request with no token | 401 |
| 6 | List users as admin | 11 total, pagination respected |
| 7 | Filter `role=courier` | 5 |
| 8 | Search `karachi` | Matched the expected operator |
| 9 | Create user | 201 |
| 10 | Duplicate email | 409 `conflict` |
| 11 | Weak password | 422 with field-level error |
| 12 | Refresh | New token pair, old token retired |
| 13 | Replay a rotated token | 401, and the newer token was revoked too |
| 14 | Courier calling `/users` | 403 `permission_denied` |
| 15 | Courier calling `/auth/me` | 200 |
| 16 | Logout, then refresh | 401 |
| 17 | Five failed logins, then correct password | 401 `account_locked` |
| 18 | Admin changing own role | 403 |
| 19 | Suspending a user | Their live access token stopped working immediately |
| 20 | Password change | Other sessions revoked; new password works; wrong current password → 403 |

Soft delete returned 204 with the row retained as `deactivated`, and readiness reported
`{"status":"ready","checks":{"postgres":"ok"}}`.

### Warehouse and shipment controllers — 30 further scenarios

| Area | Result |
|---|---|
| Warehouse list per role | admin 5 · KHI operator 1 · LHE operator 1 · support 5 · courier 4 (maintenance excluded) |
| Courier location view | Includes entrance and geofence, excludes `capacity_units` |
| Operator or support creating a warehouse | 403 |
| Operator adding a zone to another's warehouse | 403 |
| Operator reading another's warehouse | **404**, not 403 |
| Duplicate warehouse code / zone code | 409 |
| Shipment list per role | admin 7 · support 7 · KHI operator 3 · LHE operator 2 · courier 1 |
| Operator or courier creating a shipment | 403 |
| Unknown SKU / unknown warehouse | 404 with the offending id |
| Shipment from a warehouse under maintenance | 409 |
| Concurrent reference numbers | Sequential from the Postgres sequence, no collisions |
| `created → delivered` | 409, allowed transitions listed |
| Support marking ready for dispatch | 403 — wrong role |
| LHE operator marking a KHI shipment ready | 404 — out of scope |
| KHI operator marking ready | 200, version incremented |
| Courier acting on an unassigned shipment | **404** |
| Full custody chain by the assigned courier | `picked_up → in_transit → out_for_delivery → delivery_failed → out_for_delivery → delivered` |
| `in_transit → delivered` skipping a state | 409 |
| Any transition out of `delivered` | 409 — terminal |
| Delivery failure | Attempt counter incremented, reason recorded |
| Editing after dispatch | 409 |
| Packing by another warehouse's operator | 404 |
| Packing by support | 403 |
| Package sequence numbers | 1, 2 — per shipment |
| Cancelling a delivered shipment | 409 |

### Stock contention and idempotency — 12 scenarios

Detailed in §7c. Summary: first-come-first-served under row lock, all-or-nothing holds,
cancellation returns stock, expired holds reclaimed lazily, dispatch commits to a ledger entry,
and a repeated `Idempotency-Key` returns the original response with one row in the database.

### Referential integrity and deletion — 12 scenarios

Detailed in §7d. Summary: duplicate codes and emails rejected with specific messages generated
from constraint names, hard deletes of referenced rows refused by the database, soft delete
hides a row while retaining it, and restore brings it back.

### Regression after the integrity changes

| Endpoint | Result |
|---|---|
| `GET /warehouses` | 5 |
| `GET /shipments` | 7 |
| `GET /users` | 13 |
| `GET /inventory` | 30 |
| `GET /shipments` as courier CR-0001 | 1 — scoping intact |
| `GET /warehouses/locations` as courier | 4 — maintenance facility still excluded |

**All verification is manual.** These results were produced by curl against a live server and a
local database; they are evidence that the behaviour worked at that moment, not a suite that
will catch a regression tomorrow. See §13.

---

## 10b. Automated test suite

705 tests, 92% line coverage, running in about 45 seconds. They replace the manual curl passes
in §10, which proved the system worked once but could not prove it still works after a change.

| Layer | Tests | What it covers | Needs a database |
|---|---|---|---|
| `tests/unit` | 273 | State machine, tokens, hashing, access scope, error translation, every Pydantic contract | No |
| `tests/integration` | 289 | Services and repositories against real Postgres, plus constraints, migrations, seeders and concurrency | Yes |
| `tests/e2e` | 139 | HTTP through the real router, dependency graph and exception handlers | Yes |

### How the database is handled

Everything runs against **real PostgreSQL, never SQLite**. The application depends on behaviour a
substitute cannot reproduce — generated columns, native enums, schema-qualified tables, sequences
and `SELECT ... FOR UPDATE` — so a green run against SQLite would prove nothing about the code
that ships.

`tests/conftest.py` drops and rebuilds `smartlogistics_test` once per session **by running the
migrations from empty**, so every run also proves the migration chain applies to a fresh database.
It refuses to start unless the target database name ends in `_test`.

Each test then runs inside an outer transaction that is always rolled back. Services commit
freely, but those commits land on a savepoint (`join_transaction_mode="create_savepoint"`) and
vanish when the transaction unwinds. No test can see or corrupt another's data, and nothing needs
truncating between tests.

The exception is `tests/integration/test_concurrency.py`. Row locks and unique-constraint races
only exist *between* connections, so those tests commit for real and clean up after themselves.
They are marked `slow`; `make test-fast` skips them.

### What the concurrency tests establish

Four simultaneous requests for four units of a ten-unit stock position: exactly two are accepted,
`reserved_qty` lands on 8, and `reserved_qty <= on_hand_qty` holds throughout. Four simultaneous
requests carrying the same `Idempotency-Key`: exactly one is told to proceed, the rest get
`request_in_progress`, and one row is written. This is the behaviour §7c describes, now asserted
rather than argued.

### Tests that guard decisions rather than code

Several tests exist to stop a future edit quietly undoing a decision recorded in this document:

- Every `ShipmentStatus` appears in `ALLOWED_TRANSITIONS`, every status is reachable from
  `CREATED`, nothing transitions back into it, and held stock can always reach a state that
  resolves it — so the lifecycle cannot silently grow a dead end.
- Every constraint name in `CONSTRAINT_MESSAGES` really exists in the database. A renamed
  constraint would otherwise downgrade its message to the generic one with no failure anywhere.
- `alembic check` runs as a test, so a model edited without a migration fails the suite.
- The six tables carrying `deleted_at` are asserted by name, as are the partial indexes.
- `UserUpdate` and `ShipmentUpdate` are asserted **not** to declare `role`, `status` or `items` —
  the schema is what stops privilege escalation through a field the client invents.
- Every seeded account is asserted to be able to log in, which is exactly the check that caught the
  hand-edited seed data described in §6.

### Not covered

`schemas/courier.py` and `schemas/courier_assignment.py` sit at 0%: they have no controller, since
courier management is Phase 2. `seeders/runner.py` is a CLI entry point, exercised through the
seeders themselves rather than through `argparse`. `core/database.py` is at 41% because the real
session dependency is substituted in tests — the code path it replaces is a single
`async_sessionmaker` call.

---

## 10c. Packaging and deployment

The Week 1 deliverable "Docker setup operational" is met, and the same image deploys to Kubernetes.
Full detail is in [deploy/README.md](../deploy/README.md); this records the decisions.

### The image

One `Dockerfile`, four stages, with `runtime` **last** — the default build target is whatever comes
last, so putting `dev` there would quietly ship pytest and ruff to production. 352 MB for the
runtime image, 409 MB with the test toolchain.

No build toolchain is installed. Every compiled dependency — asyncpg, argon2-cffi-bindings,
pydantic-core, uvloop, httptools — publishes manylinux wheels for amd64 and arm64, verified against
the registry, so nothing compiles. Adding `build-essential` would cost several hundred megabytes and
minutes for nothing; the first attempt at this build did install it and ran the Docker VM out of
memory, which is how the question got asked.

The container runs as uid 10001, the same number the Kubernetes `securityContext` names, so the two
cannot disagree. `HEALTHCHECK` uses the interpreter rather than curl, which a slim image does not
carry.

### Migrations do not run on pod startup

`RUN_MIGRATIONS` defaults to false. Three replicas starting together would each run
`alembic upgrade head` against the same database, and concurrent DDL under one Alembic version table
deadlocks or half-applies. Compose uses a one-shot `migrate` service that the API waits on;
Kubernetes uses a Job that runs before the rollout.

### Compose

`docker compose up` needs no `.env` — every value has a working default. `DATABASE_URL` is composed
inside the compose file from the same `POSTGRES_*` variables that initialise the database, rather
than passed through from `.env`: a `.env` written for running the API on the host points at
`localhost`, which inside a container means the container itself.

Postgres is published on host port **5433** and Redis on **6380**, so the stack coexists with the
local Postgres and Redis this project has been developed against instead of failing to bind.

### Kubernetes

Plain manifests with Kustomize, no Helm. A portable `base`, plus an `aws` overlay (ECR, ALB ingress,
IRSA) and an `azure` overlay (ACR, Application Gateway, Workload Identity). Neither cloud's
specifics leak into the base.

Deliberate choices worth recording:

- **No CPU limit** on the container. Throttling an async web process adds tail latency without
  protecting anything the request has already reserved. Memory *is* capped, because overrunning it
  is a leak rather than a burst.
- **`DATABASE_POOL_SIZE` is per pod**, so the configured 10 becomes 30 across three replicas and 100
  at the HPA's ceiling of ten. Managed Postgres caps connections by instance size, and exhausting
  them takes out every replica at once.
- **`minAvailable: 2` on the PodDisruptionBudget**, below the HPA's `minReplicas: 3`, so a node
  drain cannot deadlock against the budget.
- **The Secret template is excluded from `kustomization.yaml`**, so `kubectl apply -k` cannot push
  placeholder credentials to a cluster.

### Verified, not assumed

Both images built, the stack was brought up, and: all 9 migrations applied from empty, the seeders
loaded 69 rows, a real login returned a token pair, and the full suite ran **inside** the container
(705 passed). The production image was confirmed to run as `app`, report `healthy`, and contain no
pytest. Kustomize renders 9 resources for each of the three targets, cross-checked so the Service
selector matches the pod labels, the migration Job uses the same image as the Deployment, and the
PDB leaves room under the HPA floor.

Manifests were **not** applied to a cluster. The only kubeconfig on this machine points at a real
AKS cluster with an expired credential, and pushing this at someone's live infrastructure to
validate YAML is not a reasonable thing to do unasked.

---

## 11. Defects found and fixed

| Defect | Impact | Resolution |
|---|---|---|
| **Postgres enum labels used member names** (`ADMIN`) while `server_default` used the value (`active`) | Every insert without an explicit status would fail | Added `values_callable` so labels are the lowercase values, matching the API representation |
| **`updated_at` used a database-side `onupdate`** | Forced a row re-read after every UPDATE — lazy IO that raises `MissingGreenlet` under asyncio. Every successful login returned 500 | Compute `updated_at` Python-side. `alembic check` confirms no schema drift |
| **Login example used non-existent credentials** | An imported Postman collection failed on its first request | Example now uses the seeded admin |
| Password validator passed a plain function to `field_validator` | Pydantic v2 resolves validator signatures dynamically; fragile | Replaced with an `Annotated` + `AfterValidator` type, `StrongPassword` |
| Redundant query during refresh rotation | Extra round trip | `_issue_tokens` now returns the persisted row |
| **`ShipmentService.list` shadowed the `list` builtin** inside the class body | Every `list[...]` annotation defined after it raised `TypeError` at import | `from __future__ import annotations` in both services that define a `list` method |
| **`available_qty` is a generated column** | SQLAlchemy expired it after every UPDATE and re-read it lazily — `MissingGreenlet` under asyncio, breaking every stock reservation | `eager_defaults` on the mapper, and availability computed in Python inside the service |
| Constraint names double-prefixed by the naming convention | Produced `ck_warehouses_ck_warehouses_…` | Pass bare constraint names; migration downgraded and re-applied |
| Partial indexes created in raw SQL but absent from the models | `alembic check` reported permanent drift, wanting to drop them | Declared on the models with `postgresql_where` |
| Shipment seeder pointed `courier_id` at **user** ids | Work is assigned to a fleet profile, not a login | Points at `couriers.id` |
| **A soft-deleted zone still appeared in `GET /warehouses/{id}`** | `DELETE /warehouses/zones/{id}` returned 204 and the zone kept showing up, so the delete looked like it had done nothing. The `Warehouse.zones` relationship carries no soft-delete filter | `WarehouseRepository.get` now loads zones through `selectinload(Warehouse.zones.and_(deleted_at IS NULL))`. Found by a test written for this section |
| **`WarehouseService.get` relied on the caller's session being cold** | `session.get()` returns an identity-map hit without running any loader, so a caller already holding the row got it with `zones` and `operating_hours` unloaded — and touching them raised `MissingGreenlet`. Masked in production only because each request starts a fresh session | The same repository change loads both collections explicitly, with `populate_existing` so a zone added moments earlier is reflected |
| **`constraint_name()` returned the string `"None"`** | `str(getattr(cause, "constraint_name", ""))` turns a missing constraint into the truthy `"None"`, which then failed every lookup and misled any diagnostic reading it | Return a real absence instead |
| **`/health/ready` always answered 200**, even with Postgres unreachable | It reported `{"status": "degraded"}` in the body while returning success. A readiness probe is read by its *status code*, so Kubernetes would have kept a pod that could not reach its database in the load balancer, collecting errors. Found while writing the probe configuration | Returns 503 when a dependency check fails. Verified in a container by stopping Postgres: liveness stayed 200, readiness went 503, and both recovered by themselves |
| **The default JWT signing key would deploy silently** | `change-me-in-every-non-local-environment` is in this repository. Carried into a deployed environment, anyone who has read the source could mint a valid admin token, and nothing would have warned about it | `Settings` now refuses to start when `ENVIRONMENT` is staging or production and the key is still the default. The error names the command that generates one |
| **A `constants/enums.py` importing schema names from `models.base` created a circular import** | `models.base` re-exports schema constants, but importing it triggers `models/__init__.py`, which eagerly imports every model — several of which import from `constants.enums` — before `constants.enums` finishes loading. Failed on the very first test run | Moved the seven schema-name constants to `constants/database.py`, a leaf module with no dependents of its own; `models.base` and `constants.enums` both import from it, and neither imports the other |
| **Two entries in `CONSTRAINT_MESSAGES` named constraints that do not exist** | `uq_users_email` and `ix_platform_idempotency_keys_key`; the real names are `ix_identity_users_email` and `uq_idempotency_keys_key`. Both would have fallen through to the generic message | Corrected, and a test now asserts every mapped name exists in the database |

---

## 12. Deviations from the architecture document

| Decision | Original | Now | Reason |
|---|---|---|---|
| Repository layout | Module-first (`modules/shipments/…`) | Layer-first (`controllers/`, `services/`, …) | Preference for tracking one entity per file per layer. ARCHITECTURE.md §16 updated |
| Routes folder name | `api/` | `controllers/` | Single routes layer, explicitly chosen |
| MongoDB | Containerised | Connected by URI | Existing local instance reused |
| Docker Compose | Full stack from day one | Deferred within Week 1 | Local services are faster to iterate against while the data model is still moving |
| Later-week folders | Scaffolded upfront | Deleted | Keeps review focused on the current milestone |
| Cross-schema foreign keys | Forbidden (ADR-008) | **Used** | An audit found 15 unprotected references. ADR-008 amended; extraction now needs a `DROP CONSTRAINT` first, a fair price for integrity that cannot be bypassed |
| Stock reservation timing | At dispatch, inside the Phase 2 saga | **At shipment creation**, with an expiry | Reserving only at dispatch lets two orders both be accepted for the same units, with the shortfall discovered much later — the exact inconsistency the brief complains about |
| Deletion | Status flags (`inactive`, `deactivated`) | **Soft delete** with `deleted_at` plus the status flag | Foreign keys make hard deletes fail or over-cascade; restore becomes possible (ADR-016) |
| Validation of references and uniqueness | Pre-checks in services | **Database constraints**, translated by `core/db_errors.py` | A check-then-act is a race; the constraint is authoritative |

---

## 13. Remaining Week 1 work

| Item | Notes | Priority |
|---|---|---|
| CI pipeline | Everything it would run now exists — image build, `make check`, the suite against a Postgres service container — but nothing runs them on push | **Highest** |
| Kubernetes manifests applied to a real cluster | Rendered, cross-checked and dry-run offline, but never applied. The first real deploy will surface environment-specific gaps no amount of local validation can | High |
| NetworkPolicy | Worth adding to the base once the target cluster's CNI is known | Medium |
| SKU / catalog endpoints | Model, schemas and seed data done; no controller | Medium |
| Courier endpoints | Deliberately deferred to Phase 2; data model and seed data are ready | Deferred |
| Password reset endpoints | Model and schemas exist; controller and email adapter do not | Medium |
| Audit trail | MongoDB collection and write path (ADR-002). Mongo is configured but no application code touches it yet | Medium |
| Soft-delete gaps | The three cases in §7d, plus a test forbidding `session.delete()` outside an allowlist | Low |
| Case-insensitive email | Lowercase storage is guaranteed by `UserCreate.normalise_email` alone, one layer above the unique index that depends on it. A `citext` column or a case-insensitive unique index would move the guarantee down to the database. Pinned by a test in `test_auth_service.py` | Low |
| Redis usage | Configured and running, but nothing caches or locks through it yet | Low |

---

## 14. Open questions

| # | Question | Current default |
|---|---|---|
| Q1 | Literal microservices expected for Part A? | Modular monolith (ADR-001) |
| Q2 | Qdrant or pgvector for Part B? | Qdrant (ADR-011) |
| Q3 | Which LLM provider? | Provider-agnostic adapter |
| Q4 | Real notification channel in the demo? | Mock adapters (ADR-014) |
| Q5 | Is CI part of the deliverable? | `make` targets plus a GitHub Actions workflow |
