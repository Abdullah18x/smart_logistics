from functools import lru_cache

from pydantic import Field

from sl_platform.config import ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "analytics"
    database_url: str = "postgresql+asyncpg://analytics:analytics@localhost:5446/analytics"
    jwks_url: str | None = "http://localhost:8001/.well-known/jwks.json"

    recompute_days: int = Field(
        default=2,
        description="How many days back (including today) the nightly job rebuilds the "
        "daily rollups from the milestone facts.",
    )
    summary_cache_seconds: int = Field(
        default=30,
        description="How long /summary answers are served from Redis (when REDIS_URL is set).",
    )
    recompute_hour_utc: int = Field(default=2, ge=0, le=23)
    recompute_minute_utc: int = Field(default=17, ge=0, le=59)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
