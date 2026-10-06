"""Warehouse service: facilities, zones and weekly operating hours.

Publishes full warehouse snapshots on `warehouse.events`; Shipment and
Inventory keep local read-only copies from them instead of calling here.
"""
