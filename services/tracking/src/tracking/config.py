from functools import lru_cache

from pydantic import Field

from sl_platform.config import ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "tracking"
    jwks_url: str | None = "http://localhost:8001/.well-known/jwks.json"

    mongo_url: str = "mongodb://localhost:27017"
    mongo_database: str = "tracking"

    max_batch_size: int = Field(default=200, description="Updates accepted per batch call.")
    max_clock_skew_seconds: int = Field(
        default=300, description="How far in the future a device's occurred_at may be."
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
