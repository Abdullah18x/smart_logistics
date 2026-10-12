# ADR-016: One cross-platform developer CLI

**Status:** Accepted · **Date:** 2026-10-07

## Decision

- All developer workflows live in **`scripts/dev.py`** (standard library only), run through
  thin wrappers: `dev.sh` (macOS/Linux), `dev.ps1` and `dev.cmd` (Windows). The Makefile is a
  set of aliases to it.
- `dev.sh` and `dev.ps1` run `uv sync --all-packages` first (`dev.cmd` forwards to `dev.ps1`),
  so the environment always matches the lock file; this also repairs a stale virtualenv.
- Two run modes: services on the host with infrastructure in Docker (`infra up`, `start`), or
  everything in Docker (`docker up`). `start` runs every API and worker in one terminal with
  coloured prefixes, checks the infrastructure first, and stops all children on Ctrl+C.

## Consequences

- The same commands work on every OS; nothing depends on shell features.
- New services must be registered in the CLI's service table.
