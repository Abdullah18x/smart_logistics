"""A small client for Confluent Schema Registry, JSON Schema flavour.

Subjects follow the topic-record strategy, ``<topic>-<event_type>``, because
one topic carries several event types. Every subject is set to BACKWARD
compatibility, so a consumer on the new schema can still read events written
with the previous one.

Ids are immutable, so schemas fetched by id are cached for good, and a
registration is cached per (subject, schema): registering again on every
publish costs nothing after the first.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import httpx

logger = logging.getLogger("sl_platform.schema_registry")

CONTENT_TYPE = "application/vnd.schemaregistry.v1+json"


class RegistryError(RuntimeError):
    """The registry could not be reached or answered unexpectedly."""


class IncompatibleSchemaError(RegistryError):
    """The registry refused a schema: it would break existing consumers."""


def subject_for(topic: str, event_type: str) -> str:
    return f"{topic}-{event_type}"


def _fingerprint(schema: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()


class SchemaRegistry:
    def __init__(
        self,
        url: str,
        *,
        compatibility: str = "BACKWARD",
        timeout: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.compatibility = compatibility
        self._client = httpx.AsyncClient(
            base_url=self.url,
            timeout=timeout,
            headers={"Content-Type": CONTENT_TYPE, "Accept": CONTENT_TYPE},
            transport=transport,
        )
        self._ids: dict[tuple[str, str], int] = {}
        self._by_id: dict[int, dict[str, Any]] = {}
        self._configured: set[str] = set()

    async def register(self, subject: str, schema: dict[str, Any]) -> int:
        """Register (or find) a schema under a subject; returns its global id."""
        key = (subject, _fingerprint(schema))
        if key in self._ids:
            return self._ids[key]
        await self._configure(subject)
        response = await self._request(
            "POST",
            f"/subjects/{subject}/versions",
            json={"schemaType": "JSON", "schema": json.dumps(schema)},
        )
        if response.status_code == 409:
            raise IncompatibleSchemaError(
                f"{subject}: the registry refused this schema as incompatible with the "
                f"latest version ({self.compatibility}). {response.text[:300]}"
            )
        self._raise_for(response, subject)
        schema_id = int(response.json()["id"])
        self._ids[key] = schema_id
        self._by_id[schema_id] = schema
        return schema_id

    async def schema(self, schema_id: int) -> dict[str, Any]:
        """The schema registered under an id."""
        if schema_id in self._by_id:
            return self._by_id[schema_id]
        response = await self._request("GET", f"/schemas/ids/{schema_id}")
        self._raise_for(response, f"schema {schema_id}")
        schema = json.loads(response.json()["schema"])
        self._by_id[schema_id] = schema
        return schema

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _configure(self, subject: str) -> None:
        if subject in self._configured:
            return
        response = await self._request(
            "PUT", f"/config/{subject}", json={"compatibility": self.compatibility}
        )
        self._raise_for(response, subject)
        self._configured.add(subject)

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise RegistryError(f"schema registry at {self.url} unreachable: {exc}") from exc

    @staticmethod
    def _raise_for(response: httpx.Response, what: str) -> None:
        if response.status_code >= 300:
            raise RegistryError(
                f"schema registry: {what}: HTTP {response.status_code} {response.text[:300]}"
            )
