"""Structured logs with the service name, request id and trace id on every line.

The trace id is the one to paste into Jaeger: a log line from any service
leads straight to the whole request it was part of.
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime

from sl_platform.telemetry import current_trace_ids

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if request_id := request_id_var.get():
            entry["request_id"] = request_id
        if ids := current_trace_ids():
            entry["trace_id"], entry["span_id"] = ids
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry)


class TextFormatter(logging.Formatter):
    """Human-readable lines for local runs; the trace id is appended when there is one."""

    def __init__(self, service: str) -> None:
        super().__init__(f"%(asctime)s {service} %(levelname)s %(name)s %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        if ids := current_trace_ids():
            first, newline, rest = line.partition("\n")
            line = f"{first} trace={ids[0]}{newline}{rest}"
        return line


def configure_logging(service: str, level: str = "INFO", as_json: bool = True) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(service) if as_json else TextFormatter(service))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
