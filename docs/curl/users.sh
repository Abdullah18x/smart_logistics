#!/usr/bin/env bash
# Auto-generated — do not edit. Regenerate with `make api`.
#
# Usage:
#   source docs/curl/env.sh     # sets BASE_URL and logs in
#   bash docs/curl/<file>.sh    # or copy individual commands

BASE_URL="${BASE_URL:-http://localhost:8000}"

# Create a user
curl -s -X POST "$BASE_URL/api/v1/users" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"email": "ops.lead@transfleet.com", "full_name": "Ayesha Khan", "password": "Dispatch!2026Ops", "phone": "+923001234567", "role": "warehouse_operator"}' | jq

# List users
curl -s -X GET "$BASE_URL/api/v1/users" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# Fetch a user
curl -s -X GET "$BASE_URL/api/v1/users/${user_id}" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# Update a user's profile
curl -s -X PATCH "$BASE_URL/api/v1/users/${user_id}" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"full_name": "string", "phone": "string"}' | jq

# Deactivate a user
curl -s -X DELETE "$BASE_URL/api/v1/users/${user_id}" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# Update the caller's own profile
curl -s -X PATCH "$BASE_URL/api/v1/users/me" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"full_name": "string", "phone": "string"}' | jq

# Change the caller's password
curl -s -X POST "$BASE_URL/api/v1/users/me/password" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"current_password": "string", "new_password": "string"}' | jq

# Change a user's role
curl -s -X PATCH "$BASE_URL/api/v1/users/${user_id}/role" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"role": "admin", "reason": "string"}' | jq

# Suspend, reactivate or deactivate a user
curl -s -X PATCH "$BASE_URL/api/v1/users/${user_id}/status" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"status": "pending_activation", "reason": "string"}' | jq

# Restore a soft-deleted user
curl -s -X POST "$BASE_URL/api/v1/users/${user_id}/restore" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq
