from functools import lru_cache

from pydantic import Field

from sl_platform.config import ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "notification"
    database_url: str = "postgresql+asyncpg://notification:notification@localhost:5447/notification"
    jwks_url: str | None = "http://localhost:8001/.well-known/jwks.json"

    # --- Email: real SMTP. Locally that is Mailpit, whose inbox is at :8025. ----------
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_username: str | None = None
    smtp_password: str | None = Field(default=None, repr=False)
    smtp_starttls: bool = False
    mail_from: str = "SmartLogistics <no-reply@transfleet.com>"

    # --- Who is told what ------------------------------------------------------------
    ops_email: str = Field(
        default="ops@transfleet.com",
        description="Operations mailbox: dispatch failures and other exceptions.",
    )
    public_tracking_url: str = Field(
        default="http://localhost:8000/api/v1/tracking/public/{reference_no}",
        description="Link put in customer messages; {reference_no} is filled in.",
    )

    # --- Delivery ----------------------------------------------------------------------
    max_send_attempts: int = 5
    requeue_after_seconds: int = Field(
        default=60,
        description="A notification still pending this long after it was created is "
        "queued again by the sweep (a lost enqueue, a broker outage).",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
