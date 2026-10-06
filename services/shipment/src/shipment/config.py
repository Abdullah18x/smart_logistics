from functools import lru_cache

from sl_platform.config import ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "shipment"
    database_url: str = "postgresql+asyncpg://shipment:shipment@localhost:5432/shipment"
    jwks_url: str | None = "http://localhost:8001/.well-known/jwks.json"
    inventory_service_url: str = "http://localhost:8003"
    inventory_timeout_seconds: float = 3.0


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
