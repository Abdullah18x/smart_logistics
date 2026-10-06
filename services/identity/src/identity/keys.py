"""The RS256 signing key and the JWKS document built from it.

The private key never leaves this service. In Kubernetes it is a Secret
mounted at ``JWT_PRIVATE_KEY_FILE``; every Identity pod loads the same one, so
any pod can serve the JWKS and any pod's tokens verify everywhere.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from functools import cached_property
from pathlib import Path
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from identity.config import Settings

logger = logging.getLogger("identity.keys")
ACCESS_TOKEN_TYPE = "access"


class SigningKey:
    def __init__(self, private_pem: str, kid: str) -> None:
        self.kid = kid
        self._private = serialization.load_pem_private_key(private_pem.encode(), password=None)
        self.private_pem = private_pem

    @classmethod
    def from_settings(cls, settings: Settings) -> SigningKey:
        pem = settings.jwt_private_key
        if not pem and settings.jwt_private_key_file:
            pem = Path(settings.jwt_private_key_file).read_text()
        if not pem:
            if settings.is_deployed:
                raise RuntimeError(
                    "JWT_PRIVATE_KEY or JWT_PRIVATE_KEY_FILE must be set outside local "
                    "development. Generate one with `make keys`."
                )
            logger.warning(
                "No signing key configured: generating an ephemeral one. Tokens will not "
                "survive a restart, and multiple Identity pods would disagree. Run `make keys`."
            )
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            pem = key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ).decode()
        return cls(pem, settings.jwt_key_id)

    @cached_property
    def public_pem(self) -> str:
        return (
            self._private.public_key()
            .public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            .decode()
        )

    def jwks(self) -> dict[str, Any]:
        jwk = jwt.algorithms.RSAAlgorithm.to_jwk(self._private.public_key(), as_dict=True)
        jwk.update({"kid": self.kid, "use": "sig", "alg": "RS256"})
        return {"keys": [jwk]}

    def sign_access_token(
        self,
        *,
        user_id: uuid.UUID,
        role: str,
        name: str,
        warehouse_ids: list[uuid.UUID],
        courier_id: uuid.UUID | None,
        ttl: timedelta,
        issuer: str,
        audience: str,
    ) -> str:
        now = datetime.now(UTC)
        payload = {
            "sub": str(user_id),
            "role": role,
            "name": name,
            "type": ACCESS_TOKEN_TYPE,
            # Row scoping travels in the token, so no service has to ask
            # Identity "which warehouses may this operator see?".
            "warehouse_ids": [str(w) for w in warehouse_ids],
            "courier_id": str(courier_id) if courier_id else None,
            "jti": str(uuid.uuid4()),
            "iat": now,
            "exp": now + ttl,
            "iss": issuer,
            "aud": audience,
        }
        return jwt.encode(payload, self.private_pem, algorithm="RS256", headers={"kid": self.kid})
