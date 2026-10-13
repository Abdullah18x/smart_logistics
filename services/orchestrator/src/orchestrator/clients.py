"""HTTP clients for the services the workflows drive, all as the orchestrator's
own service account: no user is behind a dispatch, so calls carry a `system`
token from Identity (client credentials), renewed before it expires."""

from __future__ import annotations

from dataclasses import dataclass

from orchestrator.config import Settings
from sl_platform.http import ServiceClient
from sl_platform.service_auth import ServiceTokenProvider


@dataclass
class Clients:
    tokens: ServiceTokenProvider
    shipment: ServiceClient
    inventory: ServiceClient
    courier: ServiceClient
    tracking: ServiceClient

    async def aclose(self) -> None:
        for client in (self.shipment, self.inventory, self.courier, self.tracking):
            await client.aclose()
        await self.tokens.aclose()


def build_clients(settings: Settings) -> Clients:
    tokens = ServiceTokenProvider(
        settings.identity_service_url, settings.service_client_id, settings.service_client_secret
    )

    def client(name: str, url: str) -> ServiceClient:
        # Longer timeout than the API default: label rendering is real work.
        return ServiceClient(name, url, timeout=10.0, service_tokens=tokens)

    return Clients(
        tokens=tokens,
        shipment=client("shipment", settings.shipment_service_url),
        inventory=client("inventory", settings.inventory_service_url),
        courier=client("courier", settings.courier_service_url),
        tracking=client("tracking", settings.tracking_service_url),
    )
