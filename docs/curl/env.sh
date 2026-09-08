#!/usr/bin/env bash
# Source this to set up a shell for manual testing:
#
#   source curl/env.sh
#
# Logs in as the seeded admin and exports ACCESS_TOKEN / REFRESH_TOKEN.

export BASE_URL="${BASE_URL:-http://localhost:8000}"
EMAIL="${EMAIL:-admin@transfleet.com}"
PASSWORD="${PASSWORD:-SmartLogistics!2026}"

_response=$(curl -s -X POST "$BASE_URL/api/v1/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}")

export ACCESS_TOKEN=$(echo "$_response" | python3 -c "import json,sys;print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null)
export REFRESH_TOKEN=$(echo "$_response" | python3 -c "import json,sys;print(json.load(sys.stdin).get('refresh_token',''))" 2>/dev/null)

if [ -n "$ACCESS_TOKEN" ]; then
  echo "logged in as $EMAIL"
  echo "BASE_URL=$BASE_URL"
  echo "ACCESS_TOKEN set (${#ACCESS_TOKEN} chars)"
else
  echo "login failed — is the server running on $BASE_URL?"
  echo "$_response"
fi
