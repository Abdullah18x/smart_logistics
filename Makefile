.DEFAULT_GOAL := help
SERVICES := identity warehouse inventory shipment
UV := uv run --no-sync

.PHONY: help install keys up down logs ps seed test test-platform test-e2e lint format check migrate-all

help:  ## Show this help
	@grep -E '^[a-z0-9-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install:  ## Create the workspace venv with every service and dev tools
	uv sync --all-packages

keys:  ## Generate Identity's RS256 signing key (infra/keys/jwt-private.pem)
	@test -f infra/keys/jwt-private.pem && echo "key exists" || \
		openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out infra/keys/jwt-private.pem
	@chmod 644 infra/keys/jwt-private.pem

up: keys  ## Build and start the whole stack
	docker compose up -d --build

down:  ## Stop the stack (add v=1 to delete volumes)
	docker compose down $(if $(v),-v,)

logs:  ## Follow logs — make logs s=shipment
	docker compose logs -f $(s)

ps:  ## Container status
	docker compose ps

seed:  ## Load development data into every service (order matters)
	docker compose exec warehouse python -m warehouse.seed
	docker compose exec identity python -m identity.seed
	docker compose exec inventory python -m inventory.seed

test:  ## Every suite: platform, each service, then cross-service e2e
	cd libs/sl-platform && $(UV) pytest tests
	@for s in $(SERVICES); do echo "== $$s"; (cd services/$$s && $(UV) pytest) || exit 1; done
	$(UV) pytest tests/e2e

test-%:  ## One service's suite — make test-shipment
	cd services/$* && $(UV) pytest

test-e2e:  ## Cross-service flows, all services in one process
	$(UV) pytest tests/e2e

lint:  ## Ruff + service-boundary contracts
	$(UV) ruff check libs services tests
	$(UV) ruff format --check libs services tests
	$(UV) lint-imports

format:  ## Format and autofix
	$(UV) ruff format libs services tests
	$(UV) ruff check --fix libs services tests

check: lint  ## Lint plus migration drift check for every service
	@for s in $(SERVICES); do (cd services/$$s && $(UV) alembic check) || exit 1; done

migrate-all:  ## Apply migrations to each service's local database
	@for s in $(SERVICES); do (cd services/$$s && $(UV) alembic upgrade head) || exit 1; done
