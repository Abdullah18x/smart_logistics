#!/usr/bin/env bash
# Auto-generated — do not edit. Regenerate with `./dev.sh docs`.
#
# Usage:
#   source docs/curl/env.sh     # sets BASE_URL (the gateway) and logs in
#   bash docs/curl/<file>.sh    # or copy individual commands

BASE_URL="${BASE_URL:-http://localhost:8000}"

# List SKUs  [inventory]
curl -s -X GET "$BASE_URL/api/v1/skus" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# Create a SKU  [inventory]
curl -s -X POST "$BASE_URL/api/v1/skus" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"category": "electronics", "code": "SKU-ELEC-0042", "currency": "PKR", "height_mm": 35, "is_fragile": true, "length_mm": 440, "name": "Wireless Keyboard", "unit_value": "4500.00", "weight_g": 650, "width_mm": 140}' | jq

# Fetch a SKU  [inventory]
curl -s -X GET "$BASE_URL/api/v1/skus/${sku_id}" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# Update a SKU  [inventory]
curl -s -X PATCH "$BASE_URL/api/v1/skus/${sku_id}" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name": "string", "description": "string", "category": "string", "weight_g": 0, "length_mm": 0, "width_mm": 0, "height_mm": 0, "is_fragile": true, "is_hazmat": true, "requires_cold_chain": true, "unit_value": 0.0, "is_active": true}' | jq
