"""HTTP layer: one router per resource, aggregated into the versioned API."""

from fastapi import APIRouter

from app.controllers import (
    auth_controller,
    health_controller,
    inventory_controller,
    shipment_controller,
    user_controller,
    warehouse_controller,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth_controller.router)
api_router.include_router(user_controller.router)
api_router.include_router(warehouse_controller.router)
api_router.include_router(shipment_controller.router)
api_router.include_router(inventory_controller.router)

# Probes sit outside the versioned prefix so orchestrators need not track versions.
health_router = health_controller.router

__all__ = ["api_router", "health_router"]
