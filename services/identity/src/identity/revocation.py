"""Identity's side of immediate token revocation (see ``sl_platform.revocation``).

Every path that revokes all of a user's refresh tokens also denylists the user's
access tokens, so sign-out-everywhere, suspension, deactivation and role or scope
changes take effect on the next request in every service, not at token expiry.

Written before the database commit: if the commit then fails, the user's current
access tokens were cut short and they sign in again — the safe direction.
A Redis outage is logged and does not fail the request: the refresh tokens are
revoked in the database regardless, and access tokens expire within minutes.
"""

from __future__ import annotations

import logging
import uuid

from identity.config import settings
from sl_platform.auth import Principal
from sl_platform.revocation import TokenDenylist

logger = logging.getLogger("identity.revocation")

denylist: TokenDenylist | None = TokenDenylist.from_settings(settings)


async def revoke_user(user_id: uuid.UUID) -> None:
    if denylist is None:
        return
    try:
        await denylist.revoke_user(user_id)
    except Exception as exc:
        logger.warning("could not denylist user %s (%s)", user_id, exc.__class__.__name__)


async def revoke_token(principal: Principal) -> None:
    """Denylist the access token the request came with (a single sign-out)."""
    if denylist is None or not principal.token_id or not principal.expires_at:
        return
    try:
        await denylist.revoke_token(principal.token_id, principal.expires_at)
    except Exception as exc:
        logger.warning("could not denylist token (%s)", exc.__class__.__name__)
