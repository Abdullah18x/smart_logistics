#!/usr/bin/env bash
# Auto-generated — do not edit. Regenerate with `./dev.sh docs`.
#
# Usage:
#   source docs/curl/env.sh     # sets BASE_URL (the gateway) and logs in
#   bash docs/curl/<file>.sh    # or copy individual commands

BASE_URL="${BASE_URL:-http://localhost:8000}"

# List stock holds  [inventory]
curl -s -X GET "$BASE_URL/api/v1/inventory/reservations" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# List stock levels  [inventory]
curl -s -X GET "$BASE_URL/api/v1/inventory" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# Open a stock position  [inventory]
curl -s -X POST "$BASE_URL/api/v1/inventory" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"on_hand_qty": 500, "reorder_level": 50, "sku_id": "00000000-0000-0000-0000-000000000000", "warehouse_id": "00000000-0000-0000-0000-000000000000"}' | jq

# Adjust on-hand stock (receipt, count, write-off)  [inventory]
curl -s -X POST "$BASE_URL/api/v1/inventory/${item_id}/adjustments" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"notes": "PO-2026-0912 received", "quantity_delta": 250, "type": "inbound_receipt"}' | jq

# Ledger for one position  [inventory]
curl -s -X GET "$BASE_URL/api/v1/inventory/${item_id}/movements" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# Release lapsed unconfirmed holds  [inventory]
curl -s -X POST "$BASE_URL/api/v1/inventory/release-expired" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq
