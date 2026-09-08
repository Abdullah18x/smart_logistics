#!/usr/bin/env bash
# Auto-generated — do not edit. Regenerate with `make api`.
#
# Usage:
#   source docs/curl/env.sh     # sets BASE_URL and logs in
#   bash docs/curl/<file>.sh    # or copy individual commands

BASE_URL="${BASE_URL:-http://localhost:8000}"

# Create a shipment
curl -s -X POST "$BASE_URL/api/v1/shipments" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"customer_reference": "ORD-2026-88123", "destination_address": {"city": "Karachi", "contact_name": "Ayesha Khan", "contact_phone": "+923001234567", "country_code": "PK", "line1": "House 12, Street 4, DHA Phase 6"}, "items": [{"quantity": 2, "sku_id": "00000000-0000-0000-0000-000000000000"}], "origin_warehouse_id": "00000000-0000-0000-0000-000000000000", "priority": "normal", "service_level": "express", "special_instructions": "Call on arrival"}' | jq

# List shipments
curl -s -X GET "$BASE_URL/api/v1/shipments" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# Fetch a shipment
curl -s -X GET "$BASE_URL/api/v1/shipments/${shipment_id}" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# Update a shipment before dispatch
curl -s -X PATCH "$BASE_URL/api/v1/shipments/${shipment_id}" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"customer_reference": "string", "service_level": "economy", "priority": "low", "promised_delivery_at": "2026-01-01T00:00:00Z", "declared_value": 0.0, "special_instructions": "string"}' | jq

# Move a shipment to the next state
curl -s -X PATCH "$BASE_URL/api/v1/shipments/${shipment_id}/status" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"status": "created", "reason": "string"}' | jq

# Cancel a shipment
curl -s -X POST "$BASE_URL/api/v1/shipments/${shipment_id}/cancel" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"reason": "string"}' | jq

# Add a parcel to a shipment
curl -s -X POST "$BASE_URL/api/v1/shipments/${shipment_id}/packages" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"height_mm": 200, "length_mm": 400, "weight_g": 1200, "width_mm": 300}' | jq

# Update a parcel
curl -s -X PATCH "$BASE_URL/api/v1/shipments/packages/${package_id}" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"weight_g": 0, "length_mm": 0, "width_mm": 0, "height_mm": 0, "status": "pending"}' | jq
