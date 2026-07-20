# Schema Source Persistence Redesign

Status: Completed

## Objective

Replace the exploratory ten-table database with one isolated `schemas` source
table and a database-independent catalog service.

## Approved scope

- Reset Alembic history to a new single-table baseline without data migration.
- Store exactly one of an absolute OpenAPI file path or verbatim raw content.
- Validate sources before creation or replacement, but do not persist parsed metadata.
- Keep SQLAlchemy behind repository and unit-of-work protocols.
- Pause and remove database-backed Planner, Memory, Context, and operation lookup flows.

## Non-goals

- Defining or persisting operation facts.
- Defining or persisting operation dependencies.
- Compatibility with databases created by the prior migrations.
- Creating a Git commit.

## Decisions

- A path is stored without a content snapshot and is reread whenever the schema is loaded.
- Schema sources support create, get, list, and whole-source replacement.
- Invalid OpenAPI input never reaches the repository.
- Direct-source operation testing remains available.

## Verification

Observed on 2026-07-19:

- `.venv/bin/python -m pytest -q tests/test_schema_catalog.py` — `9 passed`.
- `uv run pytest -q` — `62 passed`, including the real Schemathesis MCP stdio contract test.
- `.venv/bin/python -m compileall -q restscope` — exited successfully.
- `git diff --check` — exited successfully.

The implementation remains uncommitted as required by the approved scope.
