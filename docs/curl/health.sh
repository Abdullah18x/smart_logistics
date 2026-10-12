#!/usr/bin/env bash
# Auto-generated — do not edit. Regenerate with `./dev.sh docs`.
#
# Probes are per service, so they are called on each service's own port,
# not through the gateway. `./dev.sh status` does the same.

# identity: liveness, then readiness (checks its own database)
curl -s http://localhost:8001/health | jq
curl -s http://localhost:8001/health/ready | jq

# warehouse: liveness, then readiness (checks its own database)
curl -s http://localhost:8002/health | jq
curl -s http://localhost:8002/health/ready | jq

# inventory: liveness, then readiness (checks its own database)
curl -s http://localhost:8003/health | jq
curl -s http://localhost:8003/health/ready | jq

# shipment: liveness, then readiness (checks its own database)
curl -s http://localhost:8004/health | jq
curl -s http://localhost:8004/health/ready | jq
