# Operation Smoke Test Case Catalog

## Status

Implemented and locally verified on `codex/smoke-test-case-catalog`.

## Approved decision

Operation Smoke uses one in-memory `TestCaseCatalog` for the complete lifetime
of a single `OperationSmokeCoordinator.run` call. Batch execution and every
actual Solve HTTP Probe append a short `TC*` case. The Catalog is released when
the run returns and is never persisted.

Every case stores only its identity, actually sent semantic Parameter values,
an optional failed response body, and a parsed Failure. Successful cases retain
their Parameters but no response body. Only 4xx/5xx bodies are retained, with a
10 MiB limit.

Failure Dedup initially receives only the operation, exact Failure Messages,
and representative `TC*` references. It can discover the operation's Parameters
through the global `openapi.lookup_operation` capability and query exact facts
through the Agent-local `query_test_case_catalog` tool. Solve shares the same
Catalog, and its current-operation HTTP Probe may use any supported HTTP method.

Batch execution returns `BatchExecutionResult`. The former execution-report
types, private evidence joiner, and `OperationSmokeResult.batch_reports` were
deleted without compatibility aliases.

## Non-goals

- No Test Case database tables or migrations.
- No full-Test-Case or list-all-Parameters Catalog query.
- No target API or real LLM calls during implementation verification.
- No change to Generator, Patch compilation, Memory persistence, or stop
  reasons.

## Verification

- Focused Catalog, OpenAPI, Dedup, Solve Probe, Coordinator, Supervisor,
  API Behavior Monitor transport, and Batch execution regressions passed.
- `uv run pytest -q`: 511 passed, 5 skipped.
- `uv run --extra tracing pytest -q`: 511 passed, 5 skipped.
- `uv run --group evaluation pytest -q
  tests/test_operation_smoke_evaluations.py`: 13 passed.
- `uv run python -m compileall -q restscope tests evaluations`: passed.
- `git diff --check` and removed-name searches: passed.

Historical task records preserve the architecture current at their completion.
Their Batch-report and Dedup-input boundaries are superseded by this decision.
