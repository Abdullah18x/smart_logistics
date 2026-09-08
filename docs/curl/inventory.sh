#!/usr/bin/env bash
# Auto-generated — do not edit. Regenerate with `make api`.
#
# Usage:
#   source docs/curl/env.sh     # sets BASE_URL and logs in
#   bash docs/curl/<file>.sh    # or copy individual commands

BASE_URL="${BASE_URL:-http://localhost:8000}"

# List stock levels
curl -s -X GET "$BASE_URL/api/v1/inventory" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# List stock holds
curl -s -X GET "$BASE_URL/api/v1/inventory/reservations" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# Release stock holds that have lapsed
curl -s -X POST "$BASE_URL/api/v1/inventory/release-expired" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq
