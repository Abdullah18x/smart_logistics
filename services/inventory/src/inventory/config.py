from functools import lru_cache

from pydantic import Field

from sl_platform.config import ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "inventory"
    database_url: str = "postgresql+asyncpg://inventory:inventory@localhost:5432/inventory"
    jwks_url: str | None = "http://localhost:8001/.well-known/jwks.json"

    unconfirmed_hold_minutes: int = Field(
        default=30,
        description=(
            "How long a hold may stay unconfirmed. Shipment confirms it (via the "
            "shipment.created event) within seconds; a hold still unconfirmed after this "
            "belongs to a shipment that was never recorded and is reclaimed. Confirmed "
            "holds never expire."
        ),
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
