# Operation Test Agent Scaffold

## Status

Completed.

## Objective

Add the first LangGraph-based agent scaffold for testing a single OpenAPI operation with Schemathesis-backed stages.

## Scope

- Add `restscope.agent` public API and schemas.
- Add a lightweight LangGraph workflow: load operation, check capabilities, run staged tests, evaluate, report, or fail.
- Add runner abstractions for fake tests and Schemathesis MCP execution through `ToolExecutor`.
- Add the `operation_tester` tool policy role.
- Keep this scaffold read-only with respect to business fact tables.

## Verification

- `uv sync` passed.
- `uv run pytest -q` passed with 53 tests.
- `uv run python -c "from restscope.agent import OperationTestAgent"` passed.
