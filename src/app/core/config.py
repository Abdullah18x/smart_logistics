"""Application settings, loaded from the environment."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Shipped so a fresh clone runs. Rejected in staging and production.
_DEFAULT_JWT_KEY = "change-me-in-every-non-local-environment"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    app_name: str = "SmartLogistics"
    environment: Literal["local", "test", "staging", "production"] = "local"
    debug: bool = False

    # PostgreSQL
    database_url: str = Field(
        default="postgresql+asyncpg://smartlogistics:smartlogistics@localhost:5432/smartlogistics",
        description="Async SQLAlchemy DSN.",
    )
    database_echo: bool = False
    database_pool_size: int = 10
    database_max_overflow: int = 20

    # JWT
    jwt_secret_key: str = Field(
        default=_DEFAULT_JWT_KEY,
        description="HS256 signing key. Must be overridden outside local development.",
    )
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 14

    # Inventory holds
    inventory_hold_minutes: int = Field(
        default=120,
        description=(
            "How long stock stays held for an unconfirmed shipment before it can be "
            "reclaimed by another request."
        ),
    )

    # Idempotency
    idempotency_ttl_hours: int = Field(
        default=24, description="How long a replayed Idempotency-Key returns the stored response."
    )

    # Account lockout
    max_failed_login_attempts: int = 5
    account_lockout_minutes: int = 15

    # Seeding
    seed_default_password: str = Field(
        default="SmartLogistics!2026",
        description="Password assigned to every seeded account. Local use only.",
    )

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @model_validator(mode="after")
    def refuse_the_default_signing_key_outside_development(self) -> "Settings":
        """Fail to start rather than sign tokens with a key that is in the repo.

        The default exists so a developer can clone and run. Carried into a
        deployed environment it lets anyone who has read the source mint a valid
        admin token, so the process refuses to boot instead.
        """
        if (
            self.environment in ("staging", "production")
            and self.jwt_secret_key == _DEFAULT_JWT_KEY
        ):
            raise ValueError(
                "JWT_SECRET_KEY must be set to a unique value when ENVIRONMENT is "
                f"'{self.environment}'. Generate one with: openssl rand -hex 32"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
