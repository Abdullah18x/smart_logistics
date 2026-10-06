"""Shipment service: shipments, parcels, the lifecycle state machine and its audit trail.

Reserves stock through Inventory's API, keeps a local copy of warehouse facts
from events, and announces every lifecycle change on `shipment.events`.
"""
