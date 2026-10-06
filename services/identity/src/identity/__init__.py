"""Identity service: accounts, roles, sessions and the token signing key.

The only service that signs tokens and the only one that reads ``users``.
Every other service verifies tokens locally with the public key it serves at
``/.well-known/jwks.json``.
"""
