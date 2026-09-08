#!/usr/bin/env bash
# Auto-generated — do not edit. Regenerate with `make api`.
#
# Usage:
#   source docs/curl/env.sh     # sets BASE_URL and logs in
#   bash docs/curl/<file>.sh    # or copy individual commands

BASE_URL="${BASE_URL:-http://localhost:8000}"

# Authenticate and receive a token pair
curl -s -X POST "$BASE_URL/api/v1/auth/login" \
  -H 'Content-Type: application/json' \
  -d '{"email": "admin@transfleet.com", "password": "SmartLogistics!2026"}' | jq

# Exchange a refresh token for a new pair
curl -s -X POST "$BASE_URL/api/v1/auth/refresh" \
  -H 'Content-Type: application/json' \
  -d '{"refresh_token": "string"}' | jq

# Revoke the current session, or all sessions
curl -s -X POST "$BASE_URL/api/v1/auth/logout" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"refresh_token": "string"}' | jq

# List the caller's active sessions
curl -s -X GET "$BASE_URL/api/v1/auth/sessions" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# Return the authenticated user's profile
curl -s -X GET "$BASE_URL/api/v1/auth/me" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq
