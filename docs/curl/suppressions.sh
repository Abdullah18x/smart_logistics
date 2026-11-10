#!/usr/bin/env bash
# Auto-generated — do not edit. Regenerate with `./dev.sh docs`.
#
# Usage:
#   source docs/curl/env.sh     # sets BASE_URL (the gateway) and logs in
#   bash docs/curl/<file>.sh    # or copy individual commands

BASE_URL="${BASE_URL:-http://localhost:8000}"

# Suppressed addresses  [notification]
curl -s -X GET "$BASE_URL/api/v1/notifications/suppressions" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# Stop messaging an address on a channel  [notification]
curl -s -X POST "$BASE_URL/api/v1/notifications/suppressions" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"address": "+923001234567", "channel": "sms", "reason": "Asked to stop"}' | jq

# Allow an address again  [notification]
curl -s -X DELETE "$BASE_URL/api/v1/notifications/suppressions/${suppression_id}" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq
