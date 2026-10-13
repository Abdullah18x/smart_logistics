from functools import lru_cache

from pydantic import Field

from sl_platform.config import ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "orchestrator"
    jwks_url: str | None = "http://localhost:8001/.well-known/jwks.json"

    # --- Temporal ------------------------------------------------------------------
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    task_queue: str = "smartlogistics-dispatch"

    # --- Services it drives ----------------------------------------------------------
    identity_service_url: str = "http://localhost:8001"
    inventory_service_url: str = "http://localhost:8003"
    shipment_service_url: str = "http://localhost:8004"
    courier_service_url: str = "http://localhost:8005"
    tracking_service_url: str = "http://localhost:8007"

    # --- Its own service account (client credentials at Identity) -------------------
    service_client_id: str = "orchestrator"
    service_client_secret: str = Field(default="", repr=False)

    # --- Dispatch behaviour ------------------------------------------------------------
    courier_wait_attempts: int = Field(
        default=5,
        description="How many times dispatch looks for a courier before giving up.",
    )
    courier_wait_seconds: int = Field(
        default=60, description="Durable wait between courier searches."
    )

    # --- Delivery behaviour ------------------------------------------------------------
    pickup_timeout_seconds: int = Field(
        default=4 * 3600,
        description="If nobody picks a dispatched shipment up in time, reassign it (once).",
    )
    max_delivery_attempts: int = Field(
        default=3, description="Failed attempts before a return is started automatically."
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
