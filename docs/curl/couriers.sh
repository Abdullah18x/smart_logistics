#!/usr/bin/env bash
# Auto-generated — do not edit. Regenerate with `./dev.sh docs`.
#
# Usage:
#   source docs/curl/env.sh     # sets BASE_URL (the gateway) and logs in
#   bash docs/curl/<file>.sh    # or copy individual commands

BASE_URL="${BASE_URL:-http://localhost:8000}"

# Create a courier profile  [courier]
curl -s -X POST "$BASE_URL/api/v1/couriers" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"code": "CR-0101", "full_name": "Usman Tariq", "home_city": "Karachi", "max_active_shipments": 8, "max_weight_g": 30000, "phone": "+923001119999", "vehicle_plate": "KHI-4471", "vehicle_type": "motorcycle"}' | jq

# List couriers  [courier]
curl -s -X GET "$BASE_URL/api/v1/couriers" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# The calling courier's own profile  [courier]
curl -s -X GET "$BASE_URL/api/v1/couriers/me" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# Go on shift, take a break or go offline  [courier]
curl -s -X PUT "$BASE_URL/api/v1/couriers/me/availability" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"availability": "available"}' | jq

# Fetch a courier  [courier]
curl -s -X GET "$BASE_URL/api/v1/couriers/${courier_id}" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# Update a courier profile  [courier]
curl -s -X PATCH "$BASE_URL/api/v1/couriers/${courier_id}" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"full_name": "string", "phone": "string", "email": "admin@transfleet.com", "vehicle_type": "bicycle", "vehicle_plate": "string", "max_active_shipments": 0, "max_weight_g": 0, "home_city": "string", "home_warehouse_id": "00000000-0000-0000-0000-000000000000", "user_id": "00000000-0000-0000-0000-000000000000"}' | jq

# Deactivate (soft delete) a courier  [courier]
curl -s -X DELETE "$BASE_URL/api/v1/couriers/${courier_id}" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# Suspend or reactivate a courier  [courier]
curl -s -X PATCH "$BASE_URL/api/v1/couriers/${courier_id}/status" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"status": "active", "reason": "string"}' | jq

# Set a courier's availability on their behalf  [courier]
curl -s -X PUT "$BASE_URL/api/v1/couriers/${courier_id}/availability" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"availability": "available"}' | jq

# Restore a deleted courier  [courier]
curl -s -X POST "$BASE_URL/api/v1/couriers/${courier_id}/restore" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq
