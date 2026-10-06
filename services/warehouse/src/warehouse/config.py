from functools import lru_cache

from sl_platform.config import ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "warehouse"
    database_url: str = "postgresql+asyncpg://warehouse:warehouse@localhost:5432/warehouse"
    jwks_url: str | None = "http://localhost:8001/.well-known/jwks.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
