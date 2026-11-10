"""Event contracts: a JSON Schema for every event type a service publishes.

The producer owns its contracts (``<service>/contracts.py``) and they are
enforced in three places:

1. **when the event is staged** (``add_event``): the payload is checked against
   the local contract, so a producer bug fails the request that caused it, in
   tests and in production, instead of reaching another service;
2. **when the relay publishes** it: the contract is registered in the Schema
   Registry (``<topic>-<event_type>``, BACKWARD compatible) and the envelope is
   stamped with the schema id. An incompatible change is refused there;
3. **when a consumer receives** it: the payload is checked against the schema
   the id points to, so a consumer never acts on a malformed event — it goes to
   the dead-letter topic instead.

Contracts allow extra properties: producers may add optional fields without a
new consumer release (the BACKWARD rule), consumers ignore what they don't
know. Removing a field or changing its type is what the registry rejects.

The builders below keep contracts short and readable; the result is plain
JSON Schema (draft 2020-12), the same document the registry stores.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

Schema = dict[str, Any]

DRAFT = "https://json-schema.org/draft/2020-12/schema"


class ContractError(ValueError):
    """A payload broke its event contract, or an event type has none."""


# --- builders ----------------------------------------------------------------------


def string(**kw: Any) -> Schema:
    return {"type": "string", **kw}


def uuid() -> Schema:
    return {"type": "string", "format": "uuid"}


def timestamp() -> Schema:
    return {"type": "string", "format": "date-time"}


def integer(**kw: Any) -> Schema:
    return {"type": "integer", **kw}


def number(**kw: Any) -> Schema:
    return {"type": "number", **kw}


def boolean() -> Schema:
    return {"type": "boolean"}


def enum(*values: str) -> Schema:
    return {"type": "string", "enum": list(values)}


def array(items: Schema, **kw: Any) -> Schema:
    return {"type": "array", "items": items, **kw}


def nullable(schema: Schema) -> Schema:
    """The same type, or null."""
    kinds = schema.get("type")
    if isinstance(kinds, str):
        out = {**schema, "type": [kinds, "null"]}
        if "enum" in out:
            out["enum"] = [*out["enum"], None]
        return out
    return {"anyOf": [schema, {"type": "null"}]}


def obj(properties: Mapping[str, Schema], *, required: tuple[str, ...] = ()) -> Schema:
    """An object; extra properties are allowed (see the module docstring)."""
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": True,
    }


def event(
    title: str,
    properties: Mapping[str, Schema],
    *,
    required: tuple[str, ...],
    description: str = "",
) -> Schema:
    """A complete contract for one event type's payload."""
    return {
        "$schema": DRAFT,
        "title": title,
        **({"description": description} if description else {}),
        **obj(properties, required=required),
    }


def extend(base: Schema, title: str, *, required: tuple[str, ...] = (), **extra: Schema) -> Schema:
    """``base`` plus more properties — for events that carry a snapshot and a few
    event-specific fields (``previous_status``, ``reason``...)."""
    out = copy.deepcopy(base)
    out["title"] = title
    out["properties"].update(extra)
    out["required"] = [*out["required"], *required]
    return out


# --- the contracts this process knows ------------------------------------------------

_contracts: dict[str, Schema] = {}
_validators: dict[str, Draft202012Validator] = {}


def register(contracts: Mapping[str, Schema]) -> None:
    """Make a service's contracts known in this process (its events module calls
    this at import). Each schema is checked to be valid JSON Schema."""
    for event_type, schema in contracts.items():
        Draft202012Validator.check_schema(schema)
        _contracts[event_type] = schema
        _validators[event_type] = Draft202012Validator(
            schema, format_checker=Draft202012Validator.FORMAT_CHECKER
        )


def contract_for(event_type: str) -> Schema | None:
    return _contracts.get(event_type)


def known_event_types() -> list[str]:
    return sorted(_contracts)


def validate(event_type: str, payload: Any) -> None:
    """Raise ``ContractError`` if the payload breaks its contract. An event type
    without a contract is refused too, once this process knows any contracts —
    every published event must have one."""
    validator = _validators.get(event_type)
    if validator is None:
        if _contracts:
            raise ContractError(f"No contract for event type {event_type!r}.")
        return
    errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.path))
    if errors:
        raise ContractError(f"{event_type}: {describe(errors[0])}")


def validate_with(schema: Schema, payload: Any) -> None:
    """Check a payload against a schema fetched from the registry."""
    errors = sorted(
        Draft202012Validator(
            schema, format_checker=Draft202012Validator.FORMAT_CHECKER
        ).iter_errors(payload),
        key=lambda e: list(e.path),
    )
    if errors:
        raise ContractError(describe(errors[0]))


def describe(error: ValidationError) -> str:
    where = "/".join(str(p) for p in error.absolute_path) or "(payload)"
    return f"{where}: {error.message}"
