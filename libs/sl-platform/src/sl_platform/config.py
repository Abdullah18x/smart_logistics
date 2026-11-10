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
    schema_registry_url: str | None = Field(
        default=None,
        description="Confluent Schema Registry. When set, published events are registered "
        "and stamped with a schema id, and consumers validate what they receive.",
    )

    # --- Idempotency ------------------------------------------------------------
    idempotency_ttl_hours: int = 24
    idempotency_lease_seconds: int = Field(
        default=60,
        description="How long a claimed key blocks retries. A pod that dies mid-request "
        "releases its claim implicitly once the lease runs out.",
    )

    # --- Background jobs (Celery on RabbitMQ) ------------------------------------
    celery_broker_url: str = "amqp://guest:guest@localhost:5672//"

    # --- Redis: token denylist and caches ---------------------------------------------
    redis_url: str | None = Field(
        default=None,
        description="When set, every service refuses revoked access tokens (Identity writes "
        "the denylist) and may cache aggregates. Unset: signature and expiry only.",
    )
    denylist_user_ttl_seconds: int = Field(
        default=1800,
        description="How long a user-wide revocation is kept: at least the longest "
        "access-token lifetime, after which every token it covers has expired.",
    )
    denylist_fail_closed: bool = Field(
        default=False,
        description="Refuse every token while Redis is unreachable (default: allow).",
    )

    # --- Telemetry --------------------------------------------------------------
    otel_exporter_otlp_endpoint: str | None = None
    metrics_port: int | None = Field(
        default=None,
        description="Port for the Prometheus endpoint of a process without HTTP (the Kafka "
        "worker, the Celery worker). The API serves /metrics on its own port instead.",
    )

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_deployed(self) -> bool:
        return self.environment in ("staging", "production")
