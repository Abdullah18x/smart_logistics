from functools import lru_cache

from pydantic import Field

from sl_platform.config import ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "identity"
    database_url: str = "postgresql+asyncpg://identity:identity@localhost:5441/identity"

    # --- Signing key ------------------------------------------------------------
    jwt_private_key: str | None = Field(default=None, description="PEM private key.")
    jwt_private_key_file: str | None = Field(
        default=None, description="Path to a PEM private key (mounted Kubernetes Secret)."
    )
    jwt_key_id: str = Field(default="sl-1", description="Published as `kid` in the JWKS.")

    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 14

    # --- Account lockout --------------------------------------------------------
    max_failed_login_attempts: int = 5
    account_lockout_minutes: int = 15

    # --- Other services ---------------------------------------------------------
    warehouse_service_url: str = "http://localhost:8002"

    seed_default_password: str = "SmartLogistics!2026"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
