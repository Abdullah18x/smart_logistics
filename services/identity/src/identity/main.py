"""Identity HTTP process: ``uvicorn identity.main:app``."""

from identity.api import auth, users
from identity.api.deps import warehouse_client
from identity.config import settings
from identity.db import database
from identity.models import CONSTRAINT_MESSAGES
from sl_platform.app import create_service_app

app = create_service_app(
    settings=settings,
    database=database,
    title="SmartLogistics Identity",
    description=(
        "Accounts, roles and sessions. Signs RS256 access tokens and publishes the "
        "public key at `/.well-known/jwks.json`."
    ),
    routers=[auth.jwks_router, auth.router, users.router],
    tags_metadata=[
        {"name": "Authentication", "description": "Login, refresh, logout, JWKS."},
        {"name": "Users", "description": "Account management and warehouse scoping."},
    ],
    constraint_messages=CONSTRAINT_MESSAGES,
    on_shutdown=[warehouse_client.aclose],
)
