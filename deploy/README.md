# Deployment

Two ways to run SmartLogistics: Docker Compose for a machine, Kubernetes for a cluster.
Both use the same image, built from the [`Dockerfile`](../Dockerfile) at the repository root.

| Path | Use it for |
|---|---|
| [Docker Compose](#local--docker-compose) | Local development, demos, running the suite in a container |
| [Kubernetes](#kubernetes) | EKS on AWS, AKS on Azure, or any conformant cluster |

---

## The image

One `Dockerfile`, four stages. `runtime` is last, which matters: the default build target is
whatever comes last, so putting `dev` there would quietly ship pytest and ruff to production.

| Stage | Contents | Size |
|---|---|---|
| `runtime` *(default)* | Interpreter, dependencies, application. No compiler, no shell tooling | ~352 MB |
| `dev` | The above plus pytest, ruff and the test suite | ~409 MB |

```bash
docker build -t smartlogistics-api:v0.1.0 .                  # production
docker build -t smartlogistics-api:v0.1.0-dev --target dev . # with tests
```

Notable choices:

- **No build toolchain.** Every compiled dependency — asyncpg, argon2-cffi, pydantic-core, uvloop,
  httptools — publishes manylinux wheels for amd64 and arm64, so nothing compiles. Adding
  `build-essential` "to be safe" costs several hundred megabytes and minutes for nothing.
- **Non-root**, uid 10001, matching the Kubernetes `securityContext` so the two cannot disagree.
- **Dependencies are their own layer.** Editing application code reinstalls nothing.
- **`HEALTHCHECK` uses the interpreter**, because `curl` and `wget` are not in a slim image and
  adding one to run a health check is a poor trade.

### The entrypoint

[`docker/entrypoint.sh`](docker/entrypoint.sh) waits for Postgres, then optionally migrates and
seeds before handing off to the container's command.

| Variable | Default | Effect |
|---|---|---|
| `WAIT_FOR_DB` | `true` | Poll Postgres before starting. Skipped when `DATABASE_URL` is unset |
| `WAIT_TIMEOUT` | `60` | Seconds to keep trying |
| `RUN_MIGRATIONS` | `false` | Run `alembic upgrade head` first |
| `RUN_SEED` | `false` | Load development data first |

`RUN_MIGRATIONS` defaults to **false** deliberately. It is convenient for one local container, but
three replicas starting together would each migrate the same database at once, and concurrent DDL
under a single Alembic version table is how you get a deadlock or a half-applied revision. Compose
uses a one-shot `migrate` service; Kubernetes uses a Job.

---

## Local — Docker Compose

```bash
make docker-up          # Postgres, Redis, migrations, API on :8000
make docker-seed        # development data
make docker-logs
make docker-down        # add v=1 to delete the data volumes
```

Then open <http://localhost:8000/docs> and log in as `admin@transfleet.com` / `SmartLogistics!2026`.

No `.env` is required — every value has a working default. Anything you do put in `.env` overrides
it.

**Ports.** Postgres is published on **5433** and Redis on **6380**, not their usual numbers, so the
stack runs alongside a Postgres and Redis you already have rather than fighting them for the port.
Inside the compose network the API still reaches them as `postgres:5432` and `redis:6379`.

```bash
psql -h localhost -p 5433 -U smartlogistics smartlogistics
```

**Why `DATABASE_URL` is built inside the compose file.** A `.env` written for running the API on
your host points at `localhost`, which inside a container means the container itself. Compose
composes the URL from the same `POSTGRES_*` variables it uses to initialise the database, so the
two can never drift apart.

### Services

| Service | Notes |
|---|---|
| `postgres` | 16-alpine, healthchecked with the real user and database, data in a named volume |
| `redis` | 7-alpine, append-only |
| `migrate` | One-shot `alembic upgrade head`. The API waits for it to exit successfully, so a fresh `up` never races a half-migrated schema |
| `api` | Depends on all three being healthy or complete |
| `seed` | Profile `seed`. Idempotent |
| `test` | Profile `test`. Builds its own `<database>_test` and never touches the API's |

`docker-compose.override.yml` is applied automatically and swaps the API to the `dev` image with
live reload and the source bind-mounted. Only `src/` is mounted, never the virtualenv, so a `.venv`
built on macOS cannot shadow the Linux one inside the image. To run the production-shaped image
exactly as deployed:

```bash
docker compose -f docker-compose.yml up
```

---

## Kubernetes

Plain manifests and Kustomize — no Helm required, and nothing cloud-specific in the base.

```
k8s/
  base/                 portable manifests
  overlays/aws/         EKS: ECR image, ALB ingress, IRSA
  overlays/azure/       AKS: ACR image, Application Gateway, Workload Identity
```

```bash
make k8s-build CLOUD=aws     # render, apply nothing
make k8s-deploy CLOUD=aws
make k8s-migrate CLOUD=aws   # run the migration Job and wait for it
make k8s-status
```

Every value only you can know is spelled `REPLACE_ME`. Search for it before applying:

```bash
grep -rn REPLACE_ME deploy/k8s/
```

### Order of operations

1. Build and push the image to ECR or ACR with a **real tag** — never `latest`, which makes a
   rollout unreproducible and a rollback meaningless.
2. Create the Secret (below).
3. `make k8s-migrate` — schema first.
4. `make k8s-deploy` — then the pods.

### Secrets

`base/secret.example.yaml` is a template and is deliberately **not** in `kustomization.yaml`, so
`kubectl apply -k` cannot push placeholder credentials to a cluster.

```bash
kubectl -n smartlogistics create secret generic smartlogistics-api-secrets \
  --from-literal=DATABASE_URL='postgresql+asyncpg://user:pass@host:5432/smartlogistics' \
  --from-literal=JWT_SECRET_KEY="$(openssl rand -hex 32)" \
  --from-literal=REDIS_URL='redis://host:6379/0'
```

Beyond a demo, do not manage this by hand. Install the External Secrets Operator and let the
cluster pull from the platform's own store — Secrets Manager via IRSA on AWS, Key Vault via
Workload Identity on Azure. Both bind to the ServiceAccount in `base/serviceaccount.yaml`, so no
static credential is stored in the cluster at all.

The application **refuses to start** in staging or production if `JWT_SECRET_KEY` is left at its
default, so a forgotten value fails at rollout instead of quietly signing tokens with a key that is
published in this repository.

### Probes

The two probes do different jobs, and conflating them is a common way to turn a dependency outage
into a total one.

| Probe | Path | Checks | Why |
|---|---|---|---|
| `startupProbe` | `/health` | Process | Gives a slow first start time without making liveness tolerant of a hung process later |
| `livenessProbe` | `/health` | Process **only** | If this checked Postgres, a database blip would restart every pod and fix nothing |
| `readinessProbe` | `/health/ready` | Postgres | Answers **503** when it cannot reach the database, so an isolated pod leaves the load balancer instead of collecting errors |

Both cloud overlays point the external load balancer at `/health/ready` too, so the gateway and the
cluster agree about which pods can take traffic.

### What the base includes

| Resource | Notes |
|---|---|
| `Deployment` | 3 replicas, `maxUnavailable: 0`, non-root, read-only root filesystem, all capabilities dropped, zone spread |
| `Service` | ClusterIP, port 80 → 8000 |
| `Ingress` | ingress-nginx by default; overlays replace it with ALB or Application Gateway |
| `HorizontalPodAutoscaler` | 3–10 on CPU 70% / memory 80%, with a slow scale-down |
| `PodDisruptionBudget` | `minAvailable: 2`, so a node drain cannot take the whole service |
| `Job` | `alembic upgrade head`, same image as the Deployment |
| `ConfigMap` | Non-secret settings |
| `ServiceAccount` | No permissions and no token mounted; exists for the cloud identity bindings |

Two deliberate details:

- **No CPU limit.** Throttling an async web process adds tail latency without protecting anything
  the request has already reserved. Memory *is* capped, because overrunning it is a leak.
- **`DATABASE_POOL_SIZE` is per pod.** Multiply by the replica count and keep the total under what
  the managed database allows. RDS and Azure Database both cap connections by instance size, and
  exhausting them takes out every replica at once.

### Redeploying the migration Job

A Job's pod template is immutable, so re-applying one with the same name fails. `make k8s-migrate`
deletes it first. The Job also sets `ttlSecondsAfterFinished: 3600` so it usually cleans itself up.
At scale this belongs in a Helm `pre-upgrade` hook or an Argo CD `PreSync` hook, which handle
ordering for you.

### AWS (EKS)

```bash
aws ecr get-login-password --region eu-west-1 \
  | docker login --username AWS --password-stdin <account>.dkr.ecr.eu-west-1.amazonaws.com
docker build -t <account>.dkr.ecr.eu-west-1.amazonaws.com/smartlogistics-api:v0.1.0 .
docker push  <account>.dkr.ecr.eu-west-1.amazonaws.com/smartlogistics-api:v0.1.0
make k8s-deploy CLOUD=aws
```

Expects the AWS Load Balancer Controller for the ALB ingress, and IRSA for the ServiceAccount —
`eksctl create iamserviceaccount` is in the patch file's comments. Use RDS for Postgres and
ElastiCache for Redis rather than running them in the cluster; a database on ephemeral pod storage
is a data-loss incident waiting to happen.

### Azure (AKS)

```bash
az acr login --name <registry>
docker build -t <registry>.azurecr.io/smartlogistics-api:v0.1.0 .
docker push  <registry>.azurecr.io/smartlogistics-api:v0.1.0
az aks update -n smartlogistics -g <rg> --attach-acr <registry>   # once, instead of a pull secret
make k8s-deploy CLOUD=azure
```

Expects AGIC for the ingress — switch `ingressClassName` to `webapprouting.kubernetes.io` for the
managed NGINX add-on — and Workload Identity for the ServiceAccount. Use Azure Database for
PostgreSQL and Azure Cache for Redis.

---

## Not included

Honest about the edges:

- **No CI pipeline.** The image builds and the suite passes locally; nothing runs them on push.
- **No Postgres or Redis in the Kubernetes manifests**, on purpose. Both overlays assume a managed
  service. Running a database as a Deployment with an `emptyDir` loses data on reschedule.
- **No TLS certificate automation** beyond a `cert-manager` annotation in the Azure overlay.
- **No NetworkPolicy.** Worth adding once the cluster's CNI is known.
- **Mongo is configured but unused** — no application code touches it yet, so it is passed through
  and ignored.
