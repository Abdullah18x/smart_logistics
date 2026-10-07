"""Settings every service shares. Each service subclasses ``ServiceSettings``."""

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    service_name: str = "service"
    environment: Literal["local", "test", "staging", "production"] = "local"
    debug: bool = False
    log_level: str = "INFO"
    log_json: bool = True

    # --- PostgreSQL: one isolated instance per service -----------------------
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres"
    database_echo: bool = False
    database_pool_size: int = 5
    database_max_overflow: int = 10

    # --- Token verification ---------------------------------------------------
    # Identity signs RS256 access tokens; every other service only verifies them.
    jwt_issuer: str = "smartlogistics-identity"
    jwt_audience: str = "smartlogistics"
    jwks_url: str | None = Field(
        default=None,
        description="Identity's JWKS endpoint. The public key is fetched once and cached.",
    )
    jwt_public_key: str | None = Field(
        default=None,
        description="PEM public key. When set it is used instead of the JWKS endpoint "
        "(tests, or a pod that must start while Identity is unreachable).",
    )
    jwks_cache_seconds: int = 3600

    # --- Kafka ------------------------------------------------------------------
    # The host-facing listener Docker Compose publishes; containers override it.
    kafka_bootstrap_servers: str = "localhost:29092"

    # --- Idempotency ------------------------------------------------------------
    idempotency_ttl_hours: int = 24
    idempotency_lease_seconds: int = Field(
        default=60,
        description="How long a claimed key blocks retries. A pod that dies mid-request "
        "releases its claim implicitly once the lease runs out.",
    )

    # --- Telemetry --------------------------------------------------------------
    otel_exporter_otlp_endpoint: str | None = None

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_deployed(self) -> bool:
        return self.environment in ("staging", "production")
