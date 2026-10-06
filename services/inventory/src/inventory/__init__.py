"""Inventory service: SKU catalog, stock positions, holds and the stock ledger.

The single owner of stock. Shipment reserves through this service's API; no
other service can touch `inventory_items`.
"""
