#!/usr/bin/env bash
# Auto-generated — do not edit. Regenerate with `./dev.sh docs`.
#
# Usage:
#   source docs/curl/env.sh     # sets BASE_URL (the gateway) and logs in
#   bash docs/curl/<file>.sh    # or copy individual commands

BASE_URL="${BASE_URL:-http://localhost:8000}"

# Create a user  [identity]
curl -s -X POST "$BASE_URL/api/v1/users" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"email": "ops.lead@transfleet.com", "full_name": "Ayesha Khan", "password": "Dispatch!2026Ops", "phone": "+923001234567", "role": "warehouse_operator"}' | jq

# List users  [identity]
curl -s -X GET "$BASE_URL/api/v1/users" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# Update the caller's own profile  [identity]
curl -s -X PATCH "$BASE_URL/api/v1/users/me" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"full_name": "string", "phone": "string"}' | jq

# Change the caller's password  [identity]
curl -s -X POST "$BASE_URL/api/v1/users/me/password" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"current_password": "string", "new_password": "string"}' | jq

# Fetch a user  [identity]
curl -s -X GET "$BASE_URL/api/v1/users/${user_id}" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# Update a user  [identity]
curl -s -X PATCH "$BASE_URL/api/v1/users/${user_id}" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"full_name": "string", "phone": "string"}' | jq

# Deactivate (soft delete) a user  [identity]
curl -s -X DELETE "$BASE_URL/api/v1/users/${user_id}" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# Change role  [identity]
curl -s -X PATCH "$BASE_URL/api/v1/users/${user_id}/role" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"role": "admin", "reason": "string"}' | jq

# Suspend, reactivate or deactivate  [identity]
curl -s -X PATCH "$BASE_URL/api/v1/users/${user_id}/status" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"status": "pending_activation", "reason": "string"}' | jq

# Restore a soft-deleted user  [identity]
curl -s -X POST "$BASE_URL/api/v1/users/${user_id}/restore" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# Warehouses an operator is scoped to  [identity]
curl -s -X GET "$BASE_URL/api/v1/users/${user_id}/warehouses" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# Scope an operator to a warehouse  [identity]
curl -s -X POST "$BASE_URL/api/v1/users/${user_id}/warehouses" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"warehouse_id": "00000000-0000-0000-0000-000000000000"}' | jq

# Remove an operator's warehouse scope  [identity]
curl -s -X DELETE "$BASE_URL/api/v1/users/${user_id}/warehouses/${warehouse_id}" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq
