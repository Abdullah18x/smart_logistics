"""Regenerate the importable API collection from the live FastAPI routes.

Run after changing any route:

    make api

Writes into ``docs/curl/``:
  * ``openapi.json``                        — raw spec, importable anywhere
  * ``SmartLogistics.postman_collection.json``
  * ``SmartLogistics.postman_environment.json``
  * ``<tag>.sh``                            — runnable curl commands
  * ``index.html``                          — browsable reference with copy buttons

Because everything is derived from ``app.openapi()``, the collection cannot
drift from the code.
"""

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

OUTPUT_DIR = ROOT / "docs" / "curl"
BASE_URL_VAR = "{{base_url}}"
TOKEN_VAR = "{{access_token}}"

# Endpoints that must not send an Authorization header.
PUBLIC_PATHS = {"/api/v1/auth/login", "/api/v1/auth/refresh", "/health", "/health/ready"}


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
    return example_for(schema, spec)


def path_with_vars(path: str) -> str:
    """`/users/{user_id}` -> `/users/{{user_id}}` so Postman treats it as a variable."""
    return re.sub(r"\{(\w+)\}", r"{{\1}}", path)


def build_collection(spec: dict) -> dict:
    folders: dict[str, dict] = {}

    for path, operations in spec["paths"].items():
        for method, operation in operations.items():
            tag = (operation.get("tags") or ["General"])[0]
            folder = folders.setdefault(tag, {"name": tag, "item": []})

            headers = []
            body = request_body(operation, spec)
            if body is not None:
                headers.append({"key": "Content-Type", "value": "application/json"})
            if path not in PUBLIC_PATHS:
                headers.append({"key": "Authorization", "value": f"Bearer {TOKEN_VAR}"})

            query = [
                {
                    "key": p["name"],
                    "value": "",
                    "disabled": True,
                    "description": p.get("description", ""),
                }
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

            # Logging in stores the tokens straight into the environment.
            if path == "/api/v1/auth/login":
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
            folder["item"].append(item)

    return {
        "info": {
            "name": spec["info"]["title"],
            "description": (
                "Auto-generated from the OpenAPI schema by "
                "`scripts/generate_api_collection.py`. Do not edit by hand."
            ),
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "item": [folders[name] for name in sorted(folders)],
    }


def build_environment() -> dict:
    values = [
        {"key": "base_url", "value": "http://localhost:8000", "enabled": True},
        {"key": "access_token", "value": "", "enabled": True},
        {"key": "refresh_token", "value": "", "enabled": True},
        {"key": "user_id", "value": "", "enabled": True},
        {"key": "email", "value": "admin@transfleet.com", "enabled": True},
        {"key": "password", "value": "SmartLogistics!2026", "enabled": True},
    ]
    return {
        "name": "SmartLogistics — Local",
        "values": values,
        "_postman_variable_scope": "environment",
    }


def build_curl_scripts(spec: dict) -> dict[str, str]:
    """One shell script per tag, with runnable curl commands."""
    scripts: dict[str, list[str]] = {}

    for path, operations in spec["paths"].items():
        for method, operation in operations.items():
            tag = (operation.get("tags") or ["General"])[0]
            lines = scripts.setdefault(tag, [])

            summary = operation.get("summary") or f"{method.upper()} {path}"
            lines.append(f"# {summary}")

            url_path = path.replace("{", "${").replace("}", "}")
            parts = [f'curl -s -X {method.upper()} "$BASE_URL{url_path}"']
            if path not in PUBLIC_PATHS:
                parts.append('  -H "Authorization: Bearer $ACCESS_TOKEN"')

            body = request_body(operation, spec)
            if body is not None:
                parts.append("  -H 'Content-Type: application/json'")
                parts.append("  -d '" + json.dumps(body) + "'")
            lines.append(" \\\n".join(parts) + " | jq\n")

    header = (
        "#!/usr/bin/env bash\n"
        "# Auto-generated — do not edit. Regenerate with `make api`.\n"
        "#\n"
        "# Usage:\n"
        "#   source docs/curl/env.sh     # sets BASE_URL and logs in\n"
        "#   bash docs/curl/<file>.sh    # or copy individual commands\n\n"
        'BASE_URL="${BASE_URL:-http://localhost:8000}"\n\n'
    )
    return {tag: header + "\n".join(lines) for tag, lines in scripts.items()}


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


def build_html(spec: dict) -> str:
    """Render the browsable reference.

    Path parameters become variables alongside the base URL and token, so a
    reader sets them once and every command on the page is ready to paste.
    """
    from html_template import HTML_TEMPLATE

    groups: dict[str, list] = {}
    path_params: set[str] = set()

    for path, operations in spec["paths"].items():
        for name in re.findall(r"\{(\w+)\}", path):
            path_params.add(name)
        for method, operation in operations.items():
            tag = (operation.get("tags") or ["General"])[0]
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

    order = {"Authentication": 0, "Users": 1, "Warehouses": 2, "Shipments": 3, "Inventory": 4}
    data = [
        {"tag": tag, "endpoints": groups[tag]}
        for tag in sorted(groups, key=lambda t: (order.get(t, 90), t))
    ]

    variables = [
        {"name": "base_url", "label": "Base URL", "value": "http://localhost:8000"},
        {"name": "access_token", "label": "Access token", "value": "<paste after login>"},
    ] + [
        {"name": name, "label": name.replace("_", " "), "value": f"<{name}>"}
        for name in sorted(path_params)
    ]

    endpoint_count = sum(len(g["endpoints"]) for g in data)
    return (
        HTML_TEMPLATE.replace("__TITLE__", spec["info"]["title"])
        .replace("__COUNT__", str(endpoint_count))
        .replace("__DATA__", json.dumps(data, indent=None))
        .replace("__VARS__", json.dumps(variables, indent=None))
    )


def main() -> None:
    from app.main import app

    spec = app.openapi()
    OUTPUT_DIR.mkdir(exist_ok=True)

    (OUTPUT_DIR / "openapi.json").write_text(json.dumps(spec, indent=2) + "\n")
    (OUTPUT_DIR / "SmartLogistics.postman_collection.json").write_text(
        json.dumps(build_collection(spec), indent=2) + "\n"
    )
    (OUTPUT_DIR / "SmartLogistics.postman_environment.json").write_text(
        json.dumps(build_environment(), indent=2) + "\n"
    )

    for tag, content in build_curl_scripts(spec).items():
        slug = tag.lower().replace(" ", "-")
        (OUTPUT_DIR / f"{slug}.sh").write_text(content)

    (OUTPUT_DIR / "index.html").write_text(build_html(spec))

    endpoints = sum(len(ops) for ops in spec["paths"].values())
    relative = OUTPUT_DIR.relative_to(ROOT)
    print(f"wrote {endpoints} endpoints across {len(spec['paths'])} paths to {relative}/")


if __name__ == "__main__":
    main()
