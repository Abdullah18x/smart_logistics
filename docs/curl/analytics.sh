#!/usr/bin/env bash
# Auto-generated — do not edit. Regenerate with `./dev.sh docs`.
#
# Usage:
#   source docs/curl/env.sh     # sets BASE_URL (the gateway) and logs in
#   bash docs/curl/<file>.sh    # or copy individual commands

BASE_URL="${BASE_URL:-http://localhost:8000}"

# All nine metrics  [analytics]
curl -s -X GET "$BASE_URL/api/v1/analytics/summary" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# One metric per day  [analytics]
curl -s -X GET "$BASE_URL/api/v1/analytics/timeseries" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# Throughput per warehouse  [analytics]
curl -s -X GET "$BASE_URL/api/v1/analytics/warehouses" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# Per courier  [analytics]
curl -s -X GET "$BASE_URL/api/v1/analytics/couriers" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# Failed events  [analytics]
curl -s -X GET "$BASE_URL/api/v1/analytics/issues" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# Rebuild the rollups of a period  [analytics]
curl -s -X POST "$BASE_URL/api/v1/analytics/recompute" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"from": "2026-01-01", "to": "2026-01-01"}' | jq
