#!/usr/bin/env bash
# Auto-generated — do not edit. Regenerate with `make api`.
#
# Usage:
#   source docs/curl/env.sh     # sets BASE_URL and logs in
#   bash docs/curl/<file>.sh    # or copy individual commands

BASE_URL="${BASE_URL:-http://localhost:8000}"

# Liveness probe
curl -s -X GET "$BASE_URL/health" | jq

# Readiness probe — checks dependencies
curl -s -X GET "$BASE_URL/health/ready" | jq
