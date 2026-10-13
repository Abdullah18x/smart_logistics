"""Orchestrator test fixtures: a real Temporal dev server, started once.

Set TEMPORAL_CLI_PATH to an existing ``temporal`` CLI binary to skip the
download (the SDK fetches it from temporal.download otherwise).
"""

from __future__ import annotations

import os

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("LOG_JSON", "false")

import pytest
from temporalio.testing import WorkflowEnvironment


@pytest.fixture(scope="session")
async def env():
    environment = await WorkflowEnvironment.start_local(
        dev_server_existing_path=os.environ.get("TEMPORAL_CLI_PATH"),
        dev_server_log_level="error",
    )
    yield environment
    await environment.shutdown()
