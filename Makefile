# Thin aliases for ./dev.sh, which works the same on macOS, Linux and (as
# dev.ps1 / dev.cmd) Windows. Run `./dev.sh help` for every command.
.DEFAULT_GOAL := help
DEV := ./dev.sh

.PHONY: help setup keys infra-up infra-down migrate seed reset start status \
	up down ps logs docker-migrate docker-seed test lint check

help:  ## Show this help
	@grep -E '^[a-z0-9-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

setup:  ## Install the workspace and generate the signing key
	$(DEV) setup
keys:  ## Generate Identity's signing key
	$(DEV) keys

# --- local: services on this machine, infrastructure in Docker ----------------
infra-up:  ## Start the 4 databases, Kafka and Jaeger
	$(DEV) infra up
infra-down:  ## Stop them
	$(DEV) infra down
migrate:  ## Migrate every service (make migrate s="shipment inventory")
	$(DEV) migrate $(s)
seed:  ## Load development data
	$(DEV) seed
reset:  ## Roll back, migrate and seed every service
	$(DEV) reset
start:  ## Run APIs + workers in one terminal (make start s=shipment)
	$(DEV) start $(s)
status:  ## Health of every service
	$(DEV) status

# --- everything in Docker -------------------------------------------------------
up:  ## Build and start the full stack
	$(DEV) docker up
down:  ## Stop it (make down v=1 deletes volumes)
	$(DEV) docker down $(if $(v),-v,)
ps:  ## Container status
	$(DEV) docker ps
logs:  ## Follow logs (make logs s=shipment)
	$(DEV) docker logs $(s)
docker-migrate:  ## Migrate inside the containers
	$(DEV) docker migrate
docker-seed:  ## Seed inside the containers
	$(DEV) docker seed

# --- quality --------------------------------------------------------------------
test:  ## All suites (make test s=shipment for one)
	$(DEV) test $(s)
lint:  ## Ruff + service-boundary contracts
	$(DEV) lint
check:  ## Lint + migration drift per service
	$(DEV) check
