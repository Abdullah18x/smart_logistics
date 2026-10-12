"""Regenerate ``docs/database.md`` from every service's SQLAlchemy models.

Run after changing any model (``./dev.sh docs`` does it together with the API
collection). Each service's models are imported in a separate process, the
same isolation the services have at runtime, and rendered as one reference:
an ER diagram, then every table with its columns, keys and constraints.

Table descriptions are the model classes' docstrings, so the explanation of a
table lives next to its definition and cannot drift from it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "database.md"

SERVICES = {
    "identity": ("identity-db", 5441, "Accounts, sessions and operator warehouse scopes."),
    "warehouse": ("warehouse-db", 5442, "Facilities, their zones and opening hours."),
    "inventory": (
        "inventory-db",
        5443,
        "The SKU catalog, stock positions, holds, the stock ledger, a warehouse replica.",
    ),
    "shipment": (
        "shipment-db",
        5444,
        "Shipments with their items, packages and audit trail, a warehouse replica.",
    ),
}

# Runs inside each service's interpreter and prints its tables as JSON.
DUMP = r"""
import inspect, json, sys
from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.elements import TextClause

models = __import__(sys.argv[1] + ".models", fromlist=["Base"])
from sl_platform.models import PlatformBase

dialect = postgresql.dialect()

def default_of(col):
    if col.computed is not None:
        return "computed: " + str(col.computed.sqltext)
    d = col.server_default
    if d is None:
        return None
    arg = getattr(d, "arg", None)
    if isinstance(arg, TextClause):
        return arg.text
    return str(arg) if arg is not None else None

def type_of(col):
    t = col.type
    name = getattr(t, "name", None)
    if name and t.__class__.__name__ in ("Enum",):
        return name
    try:
        return t.compile(dialect=dialect).lower()
    except Exception:
        return t.__class__.__name__.lower()

def dump(base):
    docs = {}
    for mapper in base.registry.mappers:
        cls = mapper.class_
        docs[mapper.local_table.name] = inspect.getdoc(cls) or ""
    tables = []
    for table in base.metadata.sorted_tables:
        cols = []
        for col in table.columns:
            fks = []
            for fk in col.foreign_keys:
                rule = f" ({fk.ondelete.lower()})" if fk.ondelete else ""
                fks.append(f"{fk.column.table.name}.{fk.column.name}{rule}")
            cols.append({
                "name": col.name, "type": type_of(col), "nullable": bool(col.nullable),
                "pk": bool(col.primary_key), "fks": fks, "default": default_of(col),
                "unique": bool(col.unique),
            })
        checks, uniques = [], []
        for c in table.constraints:
            if isinstance(c, CheckConstraint) and c.name and not str(c.name).startswith("_"):
                checks.append({"name": str(c.name), "sql": str(c.sqltext)})
            elif isinstance(c, UniqueConstraint) and len(c.columns) > 1:
                uniques.append([col.name for col in c.columns])
        indexes = []
        for ix in table.indexes:
            where = ix.dialect_options["postgresql"].get("where")
            indexes.append({
                "name": ix.name, "unique": bool(ix.unique),
                "columns": [getattr(e, "name", str(e)) for e in ix.expressions],
                "where": str(where) if where is not None else None,
            })
        tables.append({"name": table.name, "doc": docs.get(table.name, ""), "columns": cols,
                       "checks": checks, "uniques": uniques, "indexes": indexes})
    return tables

print(json.dumps({"service": dump(models.Base), "platform": dump(PlatformBase)}))
"""


def load(service: str) -> dict:
    env = {**os.environ, "LOG_LEVEL": "ERROR"}
    result = subprocess.run(
        [sys.executable, "-c", DUMP, service],
        cwd=ROOT / "services" / service,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise SystemExit(f"could not load the {service} models")
    return json.loads(result.stdout.strip().splitlines()[-1])


# --- rendering ---------------------------------------------------------------------


def notes(col: dict) -> str:
    parts = []
    if col["pk"]:
        parts.append("primary key")
    for fk in col["fks"]:
        parts.append(f"→ `{fk.split(' ')[0]}`" + (f" {fk.split(' ', 1)[1]}" if " " in fk else ""))
    if col["unique"]:
        parts.append("unique")
    if col["default"] is not None:
        parts.append(f"default `{col['default']}`")
    return " · ".join(parts)


def table_section(table: dict, heading: str = "###") -> list[str]:
    out = [f"{heading} `{table['name']}`", ""]
    if table["doc"]:
        out += [table["doc"].replace("``", "`"), ""]
    out += ["| Column | Type | Null | Notes |", "|---|---|---|---|"]
    # Keys first, then the table's own columns, bookkeeping last.
    tail = {"created_at", "updated_at", "deleted_at", "deleted_by"}
    cols = sorted(table["columns"], key=lambda c: (not c["pk"], c["name"] in tail))
    for col in cols:
        null = "yes" if col["nullable"] else "no"
        typ = col["type"].replace("timestamp with time zone", "timestamptz")
        out.append(f"| `{col['name']}` | {typ} | {null} | {notes(col)} |")
    extra = []
    for cols_ in table["uniques"]:
        extra.append(f"- **unique** `({', '.join(cols_)})`")
    for ix in table["indexes"]:
        if ix["name"] and (ix["where"] or len(ix["columns"]) > 1):
            kind = "unique index" if ix["unique"] else "index"
            where = f" where `{ix['where']}`" if ix["where"] else ""
            extra.append(f"- **{kind}** `{ix['name']}` `({', '.join(ix['columns'])})`{where}")
    for check in table["checks"]:
        extra.append(f"- **check** `{check['name']}`: `{check['sql']}`")
    if extra:
        out += ["", *extra]
    out.append("")
    return out


def er_diagram(tables: list[dict]) -> list[str]:
    names = {t["name"] for t in tables}
    lines = ["```mermaid", "erDiagram"]
    for table in tables:
        for col in table["columns"]:
            for fk in col["fks"]:
                target = fk.split(".")[0]
                if target in names and target != table["name"]:
                    lines.append(f'    {target} ||--o{{ {table["name"]} : "{col["name"]}"')
    for table in tables:
        lines.append(f"    {table['name']} {{")
        for col in table["columns"]:
            key = " PK" if col["pk"] else (" FK" if col["fks"] else "")
            typ = col["type"].replace("timestamp with time zone", "timestamptz")
            typ = typ.split("(")[0].replace(" ", "_")
            lines.append(f"        {typ} {col['name']}{key}")
        lines.append("    }")
    lines.append("```")
    return lines


def render(data: dict[str, dict]) -> str:
    total_tables = sum(len(d["service"]) for d in data.values())
    platform = next(iter(data.values()))["platform"]
    out = [
        "# Database Reference",
        "",
        f"**Engine:** PostgreSQL 16 · **Databases:** {len(data)} (one per service) · "
        f"**Service tables:** {total_tables} · **Platform tables in each:** {len(platform)}",
        f"**Generated:** {date.today().isoformat()} by `scripts/generate_db_docs.py` "
        "(`./dev.sh docs`). Do not edit by hand.",
        "",
        "Generated from the SQLAlchemy models, which the Alembic migrations are checked "
        "against (`./dev.sh check`). Table descriptions are the model docstrings. Design "
        "rationale lives in [ARCHITECTURE.md](ARCHITECTURE.md) and the [ADRs](adr/).",
        "",
        "---",
        "",
        "## How the data is organised",
        "",
        "**One isolated PostgreSQL instance per service** "
        "([ADR-001](adr/ADR-001-microservices-database-per-service.md)). A service's tables "
        "are reachable only through its API and its events; no other service holds its "
        "credentials, and there are **no foreign keys between services**.",
        "",
        "| Service | Database (local port) | Owns | Tables |",
        "|---|---|---|---|",
    ]
    for service, d in data.items():
        container, port, owns = SERVICES[service]
        out.append(f"| **{service}** | `{container}` (:{port}) | {owns} | {len(d['service'])} |")
    out += [
        "",
        "### Conventions",
        "",
        "| Convention | Detail |",
        "|---|---|",
        "| **Primary keys** | `uuid`, generated by the application: opaque, non-enumerable, "
        "and known before the insert (a keyed shipment's id is derived from its "
        "Idempotency-Key) |",
        "| **Timestamps** | `timestamptz` everywhere; `created_at` set by the database |",
        "| **Enums** | Native Postgres enums whose labels are the lowercase API values |",
        "| **Units** | Weights in **grams**, dimensions in **millimetres**, as integers |",
        "| **Money** | `numeric(12,2)` with an explicit currency column; never floating point |",
        "| **Constraint names** | Fixed by a naming convention (`sl_platform.db`), so a "
        "violation maps to a specific API error message |",
        "| **Soft deletion** | `deleted_at` / `deleted_by` on entities a user can delete and "
        "might restore: users, warehouses, zones, SKUs, shipments |",
        "",
        "### References across services",
        "",
        "A column that names another service's row is a **plain id with no foreign key**. "
        "What would have been a cascade is an event:",
        "",
        "| Column | Refers to | Kept consistent by |",
        "|---|---|---|",
        "| `identity.user_warehouse_assignments.warehouse_id` | a warehouse | validated over "
        "REST when assigned; `warehouse.deleted` removes the scope |",
        "| `inventory.inventory_items.warehouse_id`, `shipment.shipments.origin_warehouse_id` "
        "| a warehouse | checked against the local `warehouse_refs` replica, built from "
        "`warehouse.*` events |",
        "| `inventory.inventory_reservations.shipment_id` | a shipment | the hold is confirmed "
        "on `shipment.created`, committed on dispatch, released on cancel |",
        "| `shipment.shipment_items.sku_id` | a SKU | SKU code, name and weight are "
        "**snapshotted** onto the line when stock is held |",
        "| `shipment.shipments.courier_id`, `identity.users.courier_id` | a courier profile "
        "| the Courier service (Week 2) |",
        "",
        "Display fields are copied (`origin_warehouse_code`, `sku_name`) so a read never "
        "needs another service.",
        "",
        "---",
        "",
    ]
    for service, d in data.items():
        out += [f"## {service}", "", SERVICES[service][2], ""]
        out += er_diagram(d["service"])
        out.append("")
        for table in d["service"]:
            out += table_section(table)
        out += ["---", ""]
    out += [
        "## Platform tables (in every service's database)",
        "",
        "Created by each service's first migration from `sl_platform.models`. They are "
        "what makes events and retries safe without distributed transactions.",
        "",
    ]
    for table in platform:
        out += table_section(table)
    return "\n".join(out).rstrip() + "\n"


def main() -> None:
    data = {service: load(service) for service in SERVICES}
    OUTPUT.write_text(render(data), encoding="utf-8", newline="\n")
    tables = sum(len(d["service"]) for d in data.values())
    print(f"wrote {tables} service tables from {len(data)} databases to docs/database.md")


if __name__ == "__main__":
    main()
