#!/usr/bin/env bash
# Auto-generated — do not edit. Regenerate with `./dev.sh docs`.
#
# Usage:
#   source docs/curl/env.sh     # sets BASE_URL (the gateway) and logs in
#   bash docs/curl/<file>.sh    # or copy individual commands

BASE_URL="${BASE_URL:-http://localhost:8000}"

# Hold stock for a shipment (all lines or none)  [inventory]
curl -s -X POST "$BASE_URL/api/v1/inventory/reservations" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"shipment_id": "00000000-0000-0000-0000-000000000000", "warehouse_id": "00000000-0000-0000-0000-000000000000", "lines": [{"sku_id": "00000000-0000-0000-0000-000000000000", "quantity": 0}]}' | jq

# Confirm a hold (normally driven by shipment.created)  [inventory]
curl -s -X POST "$BASE_URL/api/v1/inventory/reservations/${shipment_id}/confirm" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# Commit held stock (dispatch)  [inventory]
curl -s -X POST "$BASE_URL/api/v1/inventory/reservations/${shipment_id}/commit" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# Release held stock  [inventory]
curl -s -X POST "$BASE_URL/api/v1/inventory/reservations/${shipment_id}/release" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"reason": "Released by request"}' | jq
