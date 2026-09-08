.DEFAULT_GOAL := help
VENV := .venv/bin
PORT ?= 8000

.PHONY: help install run migrate migration seed reset lint format check test test-unit \
	test-integration test-e2e test-fast coverage api routes clean \
	docker-build docker-up docker-down docker-logs docker-shell docker-seed docker-test \
	k8s-build k8s-deploy k8s-migrate k8s-status k8s-logs

help:  ## Show this help
	@grep -E '^[a-z0-9-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install:  ## Create the venv and install dependencies
	uv venv
	uv pip install -e ".[dev]"

run:  ## Start the API with reload on $(PORT)
	$(VENV)/uvicorn app.main:app --reload --port $(PORT)

migrate:  ## Apply all migrations
	$(VENV)/alembic upgrade head

migration:  ## Generate a migration — make migration m="add shipments"
	$(VENV)/alembic revision --autogenerate -m "$(m)"

seed:  ## Load development data
	$(VENV)/python -m app.seeders.runner

reset:  ## Drop everything, re-migrate and re-seed
	$(VENV)/alembic downgrade base
	$(VENV)/alembic upgrade head
	$(VENV)/python -m app.seeders.runner

lint:  ## Lint
	$(VENV)/ruff check src tests scripts

format:  ## Format
	$(VENV)/ruff format src tests scripts
	$(VENV)/ruff check --fix src tests scripts

check:  ## Lint, format check and schema drift check
	$(VENV)/ruff check src tests scripts
	$(VENV)/ruff format --check src tests scripts
	$(VENV)/alembic check

test:  ## Run the whole test suite
	$(VENV)/pytest

test-unit:  ## Run only the unit tests (no database needed)
	$(VENV)/pytest tests/unit

test-integration:  ## Run only the service and repository tests
	$(VENV)/pytest tests/integration

test-e2e:  ## Run only the HTTP tests
	$(VENV)/pytest tests/e2e

test-fast:  ## Skip the tests that open extra database connections
	$(VENV)/pytest -m "not slow"

coverage:  ## Run the suite with a coverage report
	$(VENV)/pytest --cov=app --cov-report=term-missing --cov-report=html

api:  ## Regenerate docs/curl/ — OpenAPI spec, Postman collection, curl scripts
	$(VENV)/python scripts/generate_api_collection.py

routes:  ## Print every registered route
	@$(VENV)/python -c "from app.main import app; s=app.openapi(); [print(f'{m.upper():7} {p}') for p,o in sorted(s['paths'].items()) for m in o]"

# --- Docker -----------------------------------------------------------------

IMAGE ?= smartlogistics-api
TAG   ?= local

docker-build:  ## Build the production image
	docker build -t $(IMAGE):$(TAG) .

docker-up:  ## Start the whole stack (Postgres, Redis, migrations, API) on :8000
	docker compose up -d --build
	@echo "API on http://localhost:8000/docs"

docker-down:  ## Stop the stack. Add v=1 to delete the data volumes
	docker compose down $(if $(v),--volumes,)

docker-logs:  ## Follow the API logs
	docker compose logs -f api

docker-shell:  ## Open a shell inside the running API container
	docker compose exec api bash

docker-seed:  ## Load development data into the containerised database
	docker compose --profile seed up --exit-code-from seed seed

docker-test:  ## Run the test suite inside a container
	docker compose --profile test run --rm test

# --- Kubernetes -------------------------------------------------------------
# CLOUD selects the overlay: aws or azure.

CLOUD ?= aws
K8S_NS ?= smartlogistics

k8s-build:  ## Render the manifests without applying them
	kubectl kustomize deploy/k8s/overlays/$(CLOUD)

k8s-deploy:  ## Apply the manifests for $(CLOUD)
	kubectl apply -k deploy/k8s/overlays/$(CLOUD)

k8s-migrate:  ## Run migrations as a Job and wait for it (a Job template is immutable, so replace it)
	kubectl -n $(K8S_NS) delete job smartlogistics-migrate --ignore-not-found
	kubectl kustomize deploy/k8s/overlays/$(CLOUD) \
		| kubectl apply -n $(K8S_NS) -f - --selector app.kubernetes.io/component=migration
	kubectl -n $(K8S_NS) wait --for=condition=complete job/smartlogistics-migrate --timeout=300s

k8s-status:  ## Show what is running
	kubectl -n $(K8S_NS) get deploy,pod,svc,ingress,hpa

k8s-logs:  ## Follow the API logs across every replica
	kubectl -n $(K8S_NS) logs -l app.kubernetes.io/name=smartlogistics-api --tail=100 -f

clean:  ## Remove caches
	find . -name __pycache__ -type d -not -path './.git/*' -exec rm -rf {} + 2>/dev/null || true
	rm -rf .ruff_cache .pytest_cache
