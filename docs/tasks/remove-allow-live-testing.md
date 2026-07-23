# Remove the Run-Level Live-Testing Gate

Status: Implemented and verified (uncommitted)

## Objective

Remove the run-level `allow_live_testing` field and make invocation of
`RESTScopeApp.run()` or `OperationTestAgent.run()` the authorization to execute
Schemathesis against the App-bound target.

## User-approved scope

- Remove the field from `RESTScopeRunRequest` and `OperationTestRequest`.
- Remove the Supervisor permission check and runner-state projection.
- Allow `mcp.schemathesis.start_run` for the `operation_tester` role without a
  state flag while continuing to deny other non-read-only MCP tools.
- Reject legacy inputs through the models' existing `extra="forbid"` policy;
  do not add an alias, ignored compatibility field, or replacement gate.
- Update active examples, regression tests, and superseded task records.

## Decisions and risks

- `run()` is an execution API, not a dry-run API. A real Schemathesis runner
  can immediately send requests, including operations with side effects, to
  the target bound during App initialization.
- The MCP tool remains high-risk and approval-marked in its MCP annotations.
  RESTScope's role policy explicitly authorizes only `start_run` for the
  operation tester; other roles and other non-read-only MCP tools remain
  denied.
- Generic tool state and policy interfaces remain in place. This task removes
  only the obsolete run-level field and does not broaden into a capability API
  refactor.

## Non-goals

- No replacement approval system, environment setting, or App initialization
  switch.
- No Schemathesis MCP protocol, database, persistence, LLM, tracing, or global
  HTTP request tool changes.
- No real target or live LLM request.

## Implementation

- Request contracts now contain only their operation/task data and metadata.
- Supervisor runtime validation still checks dependency-analyzer configuration
  but no longer checks a live-testing boolean.
- Operation Agent runner state retains task identity only.
- Tool policy authorizes `mcp.schemathesis.start_run` for the operation tester
  independently of state.

## Verification

- TDD RED: six focused assertions failed against the former contracts and
  policy; the same six passed after the minimal production changes.
- Focused Supervisor, Operation Agent, policy, ToolContext, and observability
  regression suite: 39 passed.
- `uv sync && uv run pytest -q`: 184 passed, 10 skipped.
- `uv sync --extra tracing && uv run --extra tracing pytest -q`: 196 passed,
  2 skipped.
- `uv run pytest -q tests/test_schemathesis_mcp_contract.py`: 1 passed against
  the real stdio service boundary.
- `uv run --extra tracing python -m compileall -q restscope` and
  `git diff --check`: passed.
- Production code and README contain no `allow_live_testing` reference. Tests
  retain only rejection and absence assertions; the earlier ToolContext task
  record retains one explicitly superseded historical reference.
- No real target, live LLM, or Phoenix contract request was executed.
