#!/usr/bin/env bash
# Auto-generated — do not edit. Regenerate with `./dev.sh docs`.
#
# Usage:
#   source docs/curl/env.sh     # sets BASE_URL (the gateway) and logs in
#   bash docs/curl/<file>.sh    # or copy individual commands

BASE_URL="${BASE_URL:-http://localhost:8000}"

# The delivery log  [notification]
curl -s -X GET "$BASE_URL/api/v1/notifications" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# The calling courier's  [notification]
curl -s -X GET "$BASE_URL/api/v1/notifications/mine" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# One notification, with the message as sent  [notification]
curl -s -X GET "$BASE_URL/api/v1/notifications/${notification_id}" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# Send a notification again  [notification]
curl -s -X POST "$BASE_URL/api/v1/notifications/${notification_id}/resend" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq
