#!/usr/bin/env bash
# Auto-generated — do not edit. Regenerate with `./dev.sh docs`.
#
# Usage:
#   source docs/curl/env.sh     # sets BASE_URL (the gateway) and logs in
#   bash docs/curl/<file>.sh    # or copy individual commands

BASE_URL="${BASE_URL:-http://localhost:8000}"

# Public signing keys (JWKS)  [identity]
curl -s -X GET "$BASE_URL/.well-known/jwks.json" | jq

# Authenticate and receive a token pair  [identity]
curl -s -X POST "$BASE_URL/api/v1/auth/login" \
  -H 'Content-Type: application/json' \
  -d '{"email": "admin@transfleet.com", "password": "SmartLogistics!2026"}' | jq

# Exchange a refresh token for a new pair  [identity]
curl -s -X POST "$BASE_URL/api/v1/auth/refresh" \
  -H 'Content-Type: application/json' \
  -d '{"refresh_token": "string"}' | jq

# Service account: exchange client credentials for a system token  [identity]
curl -s -X POST "$BASE_URL/api/v1/auth/service-token" \
  -H 'Content-Type: application/json' \
  -d '{"client_id": "orchestrator", "client_secret": "orchestrator-dev-secret"}' | jq

# Revoke one or all sessions  [identity]
curl -s -X POST "$BASE_URL/api/v1/auth/logout" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"refresh_token": "string"}' | jq

# The caller's active sessions  [identity]
curl -s -X GET "$BASE_URL/api/v1/auth/sessions" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# The caller's profile  [identity]
curl -s -X GET "$BASE_URL/api/v1/auth/me" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq
