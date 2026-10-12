"""Regenerate ``docs/curl/`` from every service's live FastAPI routes.

Run after changing any route:

    ./dev.sh docs        (or .\\dev.ps1 docs, dev docs, make docs)

Each service's spec is produced by importing its app in a separate process
(``app.openapi()``): no service, database or Kafka has to be running. The four
specs are merged into the single API the gateway exposes on :8000, then
rendered as:

  * ``openapi.json``                         — merged spec, importable anywhere
  * ``SmartLogistics.postman_collection.json``  — one folder per service
  * ``SmartLogistics.postman_environment.json``
  * ``<tag>.sh`` and ``health.sh``           — runnable curl commands
  * ``index.html``                           — browsable reference with copy buttons

Because everything is derived from the code, the collection cannot drift from it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

OUTPUT_DIR = ROOT / "docs" / "curl"
GATEWAY_URL = "http://localhost:8000"
BASE_URL_VAR = "{{base_url}}"
TOKEN_VAR = "{{access_token}}"
KEY_FILE = ROOT / "infra" / "keys" / "jwt-private.pem"

#: service -> direct port (for health checks, which the gateway does not route)
SERVICES = {"identity": 8001, "warehouse": 8002, "inventory": 8003, "shipment": 8004}

#: Per-service probes; each service has its own, so they are not part of the gateway API.
SKIPPED_PATHS = {"/health", "/health/ready"}

# Endpoints that must not send an Authorization header.
PUBLIC_PATHS = {"/api/v1/auth/login", "/api/v1/auth/refresh", "/.well-known/jwks.json"}

TAG_ORDER = [
    "Authentication",
    "Users",
    "Warehouses",
    "Catalog",
    "Inventory",
    "Reservations",
    "Shipments",
]


# --- collecting and merging ------------------------------------------------------


def service_spec(service: str) -> dict:
    """Import the service's app in its own interpreter and dump its OpenAPI spec."""
    env = {**os.environ, "LOG_LEVEL": "ERROR", "LOG_JSON": "false"}
    if service == "identity" and KEY_FILE.exists():
        env["JWT_PRIVATE_KEY_FILE"] = str(KEY_FILE)
    code = f"import json; from {service}.main import app; print(json.dumps(app.openapi()))"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT / "services" / service,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise SystemExit(f"could not build the {service} spec")
    return json.loads(result.stdout.strip().splitlines()[-1])


def rename_schema(spec: dict, old: str, new: str) -> dict:
    text = json.dumps(spec).replace(
        f'"#/components/schemas/{old}"', f'"#/components/schemas/{new}"'
    )
    renamed = json.loads(text)
    schemas = renamed["components"]["schemas"]
    schemas[new] = schemas.pop(old)
    schemas[new]["title"] = new
    return renamed


def merge(specs: dict[str, dict]) -> dict:
    """One spec for the whole platform, as the gateway exposes it."""
    paths: dict[str, dict] = {}
    schemas: dict[str, dict] = {}
    security_schemes: dict[str, dict] = {}
    tags: dict[str, dict] = {}
    tag_service: dict[str, str] = {}

    for service, spec in specs.items():
        # Same name, different shape: prefix this service's copy (Problem and
        # ValidationError are shared through sl-platform and stay identical).
        for name, schema in list(spec.get("components", {}).get("schemas", {}).items()):
            if name in schemas and schemas[name] != schema:
                spec = rename_schema(spec, name, f"{service.title()}{name}")

        for path, operations in spec["paths"].items():
            if path in SKIPPED_PATHS:
                continue
            if path in paths:
                raise SystemExit(f"{path} is defined by two services")
            for operation in operations.values():
                operation["x-service"] = service
                for tag in operation.get("tags", []):
                    tag_service.setdefault(tag, service)
            paths[path] = operations

        components = spec.get("components", {})
        schemas.update(components.get("schemas", {}))
        security_schemes.update(components.get("securitySchemes", {}))
        for tag in spec.get("tags", []):
            if tag["name"] != "Health":
                description = tag.get("description", "")
                tags[tag["name"]] = {
                    "name": tag["name"],
                    "description": f"**{service} service** · {description}".strip(" ·"),
                }

    ordered_tags = sorted(tags.values(), key=lambda t: _tag_rank(t["name"]))
    return {
        "openapi": next(iter(specs.values()))["openapi"],
        "info": {
            "title": "SmartLogistics",
            "version": next(iter(specs.values()))["info"]["version"],
            "description": (
                "Every service's API as the gateway exposes it on :8000. Each tag "
                "is owned by one service; `x-service` on an operation names it.\n\n"
                "Log in with `POST /api/v1/auth/login`, then authorise with the "
                "`access_token`."
            ),
        },
        "servers": [{"url": GATEWAY_URL, "description": "Gateway"}],
        "tags": ordered_tags,
        "x-tagGroups": [
            {
                "name": service,
                "tags": [t["name"] for t in ordered_tags if tag_service.get(t["name"]) == service],
            }
            for service in specs
        ],
        "paths": paths,
        "components": {
            "schemas": dict(sorted(schemas.items())),
            "securitySchemes": security_schemes,
        },
    }


def _tag_rank(tag: str) -> tuple[int, str]:
    return (TAG_ORDER.index(tag) if tag in TAG_ORDER else 90, tag)


# --- examples ----------------------------------------------------------------------


def resolve(schema: dict, spec: dict, depth: int = 0) -> dict:
    """Follow a $ref, giving up on deep recursion."""
    if depth > 6:
        return {}
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        return resolve(spec["components"]["schemas"].get(name, {}), spec, depth + 1)
    for key in ("allOf", "anyOf", "oneOf"):
        if key in schema:
            for option in schema[key]:
                resolved = resolve(option, spec, depth + 1)
                if resolved.get("type") != "null":
                    return resolved
    return schema


def example_for(schema: dict, spec: dict, depth: int = 0) -> Any:
    """Build a representative request body from a JSON schema."""
    schema = resolve(schema, spec, depth)
    if depth > 5:
        return None
    if "example" in schema:
        return schema["example"]
    if schema.get("examples"):
        return schema["examples"][0]
    if "default" in schema:
        return schema["default"]
    if "enum" in schema:
        return schema["enum"][0]

    schema_type = schema.get("type")
    if schema_type == "object" or "properties" in schema:
        return {
            name: example_for(sub, spec, depth + 1)
            for name, sub in schema.get("properties", {}).items()
        }
    if schema_type == "array":
        return [example_for(schema.get("items", {}), spec, depth + 1)]
    if schema_type == "integer":
        return 0
    if schema_type == "number":
        return 0.0
    if schema_type == "boolean":
        return True

    fmt = schema.get("format")
    return {
        "uuid": "00000000-0000-0000-0000-000000000000",
        "email": "admin@transfleet.com",
        "date-time": "2026-01-01T00:00:00Z",
        "date": "2026-01-01",
        "time": "09:00:00",
    }.get(fmt, "string")


def request_body(operation: dict, spec: dict) -> Any | None:
    content = operation.get("requestBody", {}).get("content", {}).get("application/json")
    if not content:
        return None
    schema = content.get("schema", {})
    # A model-level json_schema_extra example is the most realistic payload.
    resolved = resolve(schema, spec)
    if "example" in resolved:
        return resolved["example"]
    if resolved.get("examples"):
        return resolved["examples"][0]
    return example_for(schema, spec)


def operations(spec: dict):
    for path, ops in spec["paths"].items():
        for method, operation in ops.items():
            yield path, method, operation


def tag_of(operation: dict) -> str:
    return (operation.get("tags") or ["General"])[0]


# --- Postman -------------------------------------------------------------------------


def path_with_vars(path: str) -> str:
    """`/users/{user_id}` -> `/users/{{user_id}}` so Postman treats it as a variable."""
    return re.sub(r"\{(\w+)\}", r"{{\1}}", path)


def postman_item(path: str, method: str, operation: dict, spec: dict) -> dict:
    headers = []
    body = request_body(operation, spec)
    if body is not None:
        headers.append({"key": "Content-Type", "value": "application/json"})
    if path not in PUBLIC_PATHS:
        headers.append({"key": "Authorization", "value": f"Bearer {TOKEN_VAR}"})
    if any(p.get("name") == "Idempotency-Key" for p in operation.get("parameters", [])):
        headers.append(
            {
                "key": "Idempotency-Key",
                "value": "{{$guid}}",
                "disabled": True,
                "description": "Send a unique value to make retries safe.",
            }
        )

    query = [
        {"key": p["name"], "value": "", "disabled": True, "description": p.get("description", "")}
        for p in operation.get("parameters", [])
        if p.get("in") == "query"
    ]
    raw_path = path_with_vars(path)
    item: dict[str, Any] = {
        "name": operation.get("summary") or f"{method.upper()} {path}",
        "request": {
            "method": method.upper(),
            "header": headers,
            "url": {
                "raw": BASE_URL_VAR + raw_path + ("?" if query else ""),
                "host": [BASE_URL_VAR],
                "path": [seg for seg in raw_path.split("/") if seg],
                "query": query,
            },
            "description": operation.get("description", ""),
        },
    }
    if body is not None:
        item["request"]["body"] = {
            "mode": "raw",
            "raw": json.dumps(body, indent=2),
            "options": {"raw": {"language": "json"}},
        }
    if path in ("/api/v1/auth/login", "/api/v1/auth/refresh"):
        # Logging in stores the tokens straight into the environment.
        item["event"] = [
            {
                "listen": "test",
                "script": {
                    "type": "text/javascript",
                    "exec": [
                        "if (pm.response.code === 200) {",
                        "  const body = pm.response.json();",
                        "  pm.environment.set('access_token', body.access_token);",
                        "  pm.environment.set('refresh_token', body.refresh_token);",
                        "}",
                    ],
                },
            }
        ]
    return item


def build_collection(spec: dict) -> dict:
    services: dict[str, dict[str, list]] = {}
    for path, method, operation in operations(spec):
        tags = services.setdefault(operation["x-service"], {})
        tags.setdefault(tag_of(operation), []).append(postman_item(path, method, operation, spec))

    return {
        "info": {
            "name": spec["info"]["title"],
            "description": (
                "Auto-generated from every service's OpenAPI schema by "
                "`scripts/generate_api_docs.py` (`./dev.sh docs`). Do not edit by hand.\n\n"
                "All requests go through the gateway (`base_url`, port 8000)."
            ),
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "item": [
            {
                "name": f"{service} service",
                "item": [
                    {"name": tag, "item": items}
                    for tag, items in sorted(tags.items(), key=lambda kv: _tag_rank(kv[0]))
                ],
            }
            for service, tags in services.items()
        ],
    }


def build_environment(spec: dict) -> dict:
    path_params = sorted(
        {name for path in spec["paths"] for name in re.findall(r"\{(\w+)\}", path)}
    )
    values = [
        {"key": "base_url", "value": GATEWAY_URL, "enabled": True},
        {"key": "access_token", "value": "", "enabled": True},
        {"key": "refresh_token", "value": "", "enabled": True},
        *({"key": name, "value": "", "enabled": True} for name in path_params),
        {"key": "email", "value": "admin@transfleet.com", "enabled": True},
        {"key": "password", "value": "SmartLogistics!2026", "enabled": True},
    ]
    return {
        "name": "SmartLogistics — Local",
        "values": values,
        "_postman_variable_scope": "environment",
    }


# --- curl ----------------------------------------------------------------------------

SCRIPT_HEADER = (
    "#!/usr/bin/env bash\n"
    "# Auto-generated — do not edit. Regenerate with `./dev.sh docs`.\n"
    "#\n"
    "# Usage:\n"
    "#   source docs/curl/env.sh     # sets BASE_URL (the gateway) and logs in\n"
    "#   bash docs/curl/<file>.sh    # or copy individual commands\n\n"
    'BASE_URL="${BASE_URL:-http://localhost:8000}"\n\n'
)


def build_curl_scripts(spec: dict) -> dict[str, str]:
    """One shell script per tag, with runnable curl commands."""
    scripts: dict[str, list[str]] = {}
    for path, method, operation in operations(spec):
        lines = scripts.setdefault(tag_of(operation), [])
        summary = operation.get("summary") or f"{method.upper()} {path}"
        lines.append(f"# {summary}  [{operation['x-service']}]")

        url_path = path.replace("{", "${")
        parts = [f'curl -s -X {method.upper()} "$BASE_URL{url_path}"']
        if path not in PUBLIC_PATHS:
            parts.append('  -H "Authorization: Bearer $ACCESS_TOKEN"')
        body = request_body(operation, spec)
        if body is not None:
            parts.append("  -H 'Content-Type: application/json'")
            parts.append("  -d '" + json.dumps(body) + "'")
        lines.append(" \\\n".join(parts) + " | jq\n")

    return {tag: SCRIPT_HEADER + "\n".join(lines) for tag, lines in scripts.items()}


def build_health_script() -> str:
    lines = [
        "#!/usr/bin/env bash",
        "# Auto-generated — do not edit. Regenerate with `./dev.sh docs`.",
        "#",
        "# Probes are per service, so they are called on each service's own port,",
        "# not through the gateway. `./dev.sh status` does the same.",
        "",
    ]
    for service, port in SERVICES.items():
        lines += [
            f"# {service}: liveness, then readiness (checks its own database)",
            f"curl -s http://localhost:{port}/health | jq",
            f"curl -s http://localhost:{port}/health/ready | jq",
            "",
        ]
    return "\n".join(lines)


def curl_command(path: str, method: str, operation: dict, spec: dict) -> str:
    """A single, copy-ready curl command using ``{{variable}}`` placeholders."""
    url_path = re.sub(r"\{(\w+)\}", r"{{\1}}", path)
    parts = [f'curl -X {method.upper()} "{{{{base_url}}}}{url_path}"']
    if path not in PUBLIC_PATHS:
        parts.append('  -H "Authorization: Bearer {{access_token}}"')
    body = request_body(operation, spec)
    if body is not None:
        parts.append("  -H 'Content-Type: application/json'")
        parts.append("  -d '" + json.dumps(body, indent=2) + "'")
    return " \\\n".join(parts)


# --- HTML ----------------------------------------------------------------------------


def build_html(spec: dict) -> str:
    """Render the browsable reference.

    Path parameters become variables alongside the base URL and token, so a
    reader sets them once and every command on the page is ready to paste.
    """
    from html_template import HTML_TEMPLATE

    groups: dict[str, list] = {}
    path_params: set[str] = set()
    owner: dict[str, str] = {}

    for path, method, operation in operations(spec):
        path_params.update(re.findall(r"\{(\w+)\}", path))
        tag = tag_of(operation)
        owner.setdefault(tag, operation["x-service"])
        groups.setdefault(tag, []).append(
            {
                "method": method.upper(),
                "path": path,
                "summary": operation.get("summary") or "",
                "description": (operation.get("description") or "").strip(),
                "auth": path not in PUBLIC_PATHS,
                "curl": curl_command(path, method, operation, spec),
                "query": [
                    p["name"] for p in operation.get("parameters", []) if p.get("in") == "query"
                ],
            }
        )

    data = [
        {"tag": f"{tag} · {owner[tag]} service", "endpoints": groups[tag]}
        for tag in sorted(groups, key=_tag_rank)
    ]
    variables = [
        {"name": "base_url", "label": "Base URL", "value": GATEWAY_URL},
        {"name": "access_token", "label": "Access token", "value": "<paste after login>"},
    ] + [
        {"name": name, "label": name.replace("_", " "), "value": f"<{name}>"}
        for name in sorted(path_params)
    ]

    count = sum(len(g["endpoints"]) for g in data)
    return (
        HTML_TEMPLATE.replace("__TITLE__", spec["info"]["title"])
        .replace("__COUNT__", str(count))
        .replace("__DATA__", json.dumps(data, indent=None))
        .replace("__VARS__", json.dumps(variables, indent=None))
    )


# --- main ----------------------------------------------------------------------------


def write(name: str, content: str) -> None:
    # newline="\n": identical files on every OS, so regenerating never shows a diff.
    (OUTPUT_DIR / name).write_text(content, encoding="utf-8", newline="\n")


def main() -> None:
    specs = {service: service_spec(service) for service in SERVICES}
    spec = merge(specs)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    write("openapi.json", json.dumps(spec, indent=2, ensure_ascii=False) + "\n")
    write(
        "SmartLogistics.postman_collection.json",
        json.dumps(build_collection(spec), indent=2, ensure_ascii=False) + "\n",
    )
    write(
        "SmartLogistics.postman_environment.json",
        json.dumps(build_environment(spec), indent=2, ensure_ascii=False) + "\n",
    )
    for tag, content in build_curl_scripts(spec).items():
        write(f"{tag.lower().replace(' ', '-')}.sh", content)
    write("health.sh", build_health_script())
    write("index.html", build_html(spec))

    count = sum(1 for _ in operations(spec))
    print(
        f"wrote {count} endpoints from {len(specs)} services to "
        f"{OUTPUT_DIR.relative_to(ROOT).as_posix()}/"
    )


if __name__ == "__main__":
    main()
