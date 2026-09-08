#!/usr/bin/env bash
# Auto-generated — do not edit. Regenerate with `make api`.
#
# Usage:
#   source docs/curl/env.sh     # sets BASE_URL and logs in
#   bash docs/curl/<file>.sh    # or copy individual commands

BASE_URL="${BASE_URL:-http://localhost:8000}"

# Navigation view of active warehouses
curl -s -X GET "$BASE_URL/api/v1/warehouses/locations" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# List warehouses
curl -s -X GET "$BASE_URL/api/v1/warehouses" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# Create a warehouse
curl -s -X POST "$BASE_URL/api/v1/warehouses" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"address_line1": "Plot 42, Korangi Industrial Area", "capacity_units": 50000, "city": "Karachi", "code": "KHI-02", "contact_email": "khi02@transfleet.com", "contact_name": "Imran Sheikh", "contact_phone": "+922135000002", "country_code": "PK", "entrance_latitude": 24.8611, "entrance_longitude": 67.0018, "geofence_radius_m": 150, "latitude": 24.8607, "longitude": 67.0011, "max_daily_outbound": 4000, "name": "Karachi North Fulfilment Centre", "postal_code": "74900", "region": "Sindh", "timezone": "Asia/Karachi", "type": "fulfillment_center"}' | jq

# Compact facility list for support staff
curl -s -X GET "$BASE_URL/api/v1/warehouses/summary" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# Fetch a warehouse with zones and schedule
curl -s -X GET "$BASE_URL/api/v1/warehouses/${warehouse_id}" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# Update a warehouse
curl -s -X PATCH "$BASE_URL/api/v1/warehouses/${warehouse_id}" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name": "string", "type": "fulfillment_center", "address_line1": "string", "address_line2": "string", "city": "string", "region": "string", "postal_code": "string", "country_code": "string", "latitude": 0.0, "longitude": 0.0, "entrance_latitude": 0.0, "entrance_longitude": 0.0, "map_place_id": "string", "geofence_radius_m": 0, "capacity_units": 0, "max_daily_outbound": 0, "timezone": "string", "contact_name": "string", "contact_email": "admin@transfleet.com", "contact_phone": "string", "notes": "string"}' | jq

# Deactivate a warehouse
curl -s -X DELETE "$BASE_URL/api/v1/warehouses/${warehouse_id}" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# Change operational status
curl -s -X PATCH "$BASE_URL/api/v1/warehouses/${warehouse_id}/status" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"status": "active", "reason": "string"}' | jq

# Restore a soft-deleted warehouse
curl -s -X POST "$BASE_URL/api/v1/warehouses/${warehouse_id}/restore" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# Add a zone
curl -s -X POST "$BASE_URL/api/v1/warehouses/${warehouse_id}/zones" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"capacity_units": 5000, "code": "A1", "name": "Ambient Storage A1", "type": "storage"}' | jq

# Update a zone
curl -s -X PATCH "$BASE_URL/api/v1/warehouses/zones/${zone_id}" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name": "string", "type": "receiving", "capacity_units": 0, "is_active": true}' | jq

# Remove a zone
curl -s -X DELETE "$BASE_URL/api/v1/warehouses/zones/${zone_id}" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# Weekly schedule
curl -s -X GET "$BASE_URL/api/v1/warehouses/${warehouse_id}/operating-hours" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq

# Replace the weekly schedule
curl -s -X PUT "$BASE_URL/api/v1/warehouses/${warehouse_id}/operating-hours" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"days": [{"closes_at": "20:00:00", "day_of_week": 1, "opens_at": "08:00:00"}]}' | jq
