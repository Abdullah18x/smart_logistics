"""Shared plumbing for SmartLogistics services.

Every service depends on this library and on nothing else from the monorepo.
It holds mechanisms only — auth, persistence, idempotency, the outbox, event
consumption, inter-service HTTP — never domain rules. A rule about shipments
belongs in the shipment service, even if two services seem to need it.
"""

__version__ = "0.1.0"
