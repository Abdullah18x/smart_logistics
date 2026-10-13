#!/usr/bin/env bash
# Auto-generated — do not edit. Regenerate with `./dev.sh docs`.
#
# Usage:
#   source docs/curl/env.sh     # sets BASE_URL (the gateway) and logs in
#   bash docs/curl/<file>.sh    # or copy individual commands

BASE_URL="${BASE_URL:-http://localhost:8000}"

# Progress of one dispatch workflow  [orchestrator]
curl -s -X GET "$BASE_URL/api/v1/dispatches/${workflow_id}" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq
