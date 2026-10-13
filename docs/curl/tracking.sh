#!/usr/bin/env bash
# Auto-generated — do not edit. Regenerate with `./dev.sh docs`.
#
# Usage:
#   source docs/curl/env.sh     # sets BASE_URL (the gateway) and logs in
#   bash docs/curl/<file>.sh    # or copy individual commands

BASE_URL="${BASE_URL:-http://localhost:8000}"

# Open a shipment's journey (dispatch workflow)  [tracking]
curl -s -X POST "$BASE_URL/api/v1/tracking/journeys" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"shipment_id": "00000000-0000-0000-0000-000000000000", "reference_no": "string", "courier_id": "00000000-0000-0000-0000-000000000000", "origin_warehouse_id": "00000000-0000-0000-0000-000000000000", "destination_city": "string"}' | jq

# Cancel a journey (dispatch compensation)  [tracking]
curl -s -X POST "$BASE_URL/api/v1/tracking/journeys/${shipment_id}/cancel" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"reason": "string"}' | jq

# A shipment's journey with its timeline  [tracking]
curl -s -X GET "$BASE_URL/api/v1/tracking/journeys/${shipment_id}" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# Report a field update (courier)  [tracking]
curl -s -X POST "$BASE_URL/api/v1/tracking/updates" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"event_id": "0f8fad5b-d9cb-469f-a165-70867728950e", "location": {"latitude": 24.8607, "longitude": 67.0011}, "milestone": "picked_up", "occurred_at": "2026-10-12T09:15:00Z", "shipment_id": "00000000-0000-0000-0000-000000000000", "type": "milestone"}' | jq

# Report many field updates at once (offline sync)  [tracking]
curl -s -X POST "$BASE_URL/api/v1/tracking/updates/batch" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"updates": [{"event_id": "0f8fad5b-d9cb-469f-a165-70867728950e", "location": {"latitude": 24.8607, "longitude": 67.0011}, "milestone": "picked_up", "occurred_at": "2026-10-12T09:15:00Z", "shipment_id": "00000000-0000-0000-0000-000000000000", "type": "milestone"}]}' | jq

# Public tracking page data (no login)  [tracking]
curl -s -X GET "$BASE_URL/api/v1/tracking/public/${reference_no}" | jq
