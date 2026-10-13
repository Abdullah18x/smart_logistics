#!/usr/bin/env bash
# Auto-generated — do not edit. Regenerate with `./dev.sh docs`.
#
# Usage:
#   source docs/curl/env.sh     # sets BASE_URL (the gateway) and logs in
#   bash docs/curl/<file>.sh    # or copy individual commands

BASE_URL="${BASE_URL:-http://localhost:8000}"

# Assign a courier to a shipment (automatic or chosen)  [courier]
curl -s -X POST "$BASE_URL/api/v1/couriers/assignments" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"shipment_id": "00000000-0000-0000-0000-000000000000", "total_weight_g": 2400, "warehouse_id": "00000000-0000-0000-0000-000000000000"}' | jq

# List assignments  [courier]
curl -s -X GET "$BASE_URL/api/v1/couriers/assignments" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# The calling courier's jobs  [courier]
curl -s -X GET "$BASE_URL/api/v1/couriers/assignments/mine" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# Hand a shipment to another courier (manual override)  [courier]
curl -s -X POST "$BASE_URL/api/v1/couriers/assignments/${shipment_id}/reassign" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"reason": "Courier's vehicle broke down"}' | jq

# Release a shipment's courier (dispatch compensation)  [courier]
curl -s -X POST "$BASE_URL/api/v1/couriers/assignments/${shipment_id}/release" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"reason": "string"}' | jq
