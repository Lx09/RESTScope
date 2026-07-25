# App ToolContext and OpenAPI IR Retrieval

Status: Superseded

The ToolContext and one-time IR initialization parts remain active. The
OpenAPI Retrieval Agent, its public capability, and its internal investigation
tools were deleted by `task-focused-main-flow-prompts.md`; the implementation
and verification below are retained as historical evidence only.

## Objective

Initialize one OpenAPI target per `RESTScopeApp`, bind its parsed IR and runtime
target settings to a shared `ToolContext`, and make OpenAPI Retrieval operate
only on that IR without reparsing files or using raw-text fallback.

## User-approved scope

- Add `RESTScopeApp.initialize(schema_source=..., base_url=..., headers=...)`.
- Keep only `allow_live_testing` and `metadata` on `RESTScopeRunRequest` at the
  time of this implementation. The run-level live-testing field was removed by
  the later decision recorded in `remove-allow-live-testing.md`.
- Export `ToolContext` from `restscope.capabilities` and bind it once to the
  App's `ToolExecutor`.
- Inject context out-of-band into trusted handlers as
  `handler(context, /, **arguments)` without exposing it to tool schemas or
  model arguments.
- Make Supervisor and OperationTest use the bound IR and remove target/schema
  values from LangGraph state and operation request/target models.
- Project schema source, base URL, and headers only into Schemathesis
  `start_run` arguments.
- Replace File Retrieval with `OpenAPIRetrievalAgent` and
  `restscope.openapi.retrieve`, with no compatibility aliases.
- Remove retrieval file loading, raw document/text search, external-reference
  checks, and text evidence. Scan IR directly on every symbol search.
- Preserve 2xx filtering, nested field expansion, and strict operation/evidence
  consistency validation.
- Update public documentation and mark the File Retrieval v1 task superseded.

## Non-goals

- Database or persistence changes.
- Immutable IR conversion, baseline drift detection, or schema variant APIs.
- Live LLM calls or tests against a real target.
- Push or pull request creation.

## Decisions and assumptions

- One App instance binds one OpenAPI schema and target environment.
- Local registered handlers are trusted; context is not a model-controlled
  argument.
- The IR is shared under a read-only convention for this iteration.
- Baseline file drift between initialization and Schemathesis reading remains a
  known follow-up risk.
- Failed initialization leaves the App retryable; successful initialization is
  one-time and closing the App clears both references.

## Implemented

- Added copied, read-only mappings for baseline schema source and headers, with
  headers excluded from `ToolContext` repr.
- Added context binding, access, clearing, and redacted handler-error handling
  to `ToolExecutor`.
- Moved parsing and OpenAPI validation to App initialization and removed graph
  reparsing.
- Reduced Supervisor and operation contracts and changed Schemathesis argument
  construction to read the executor context only at `start_run`.
- Renamed the Agent package and public contracts to `openapi_retrieval`, and
  retained only five IR-backed internal tools with request-scoped evidence.
- Added tests for initialization sources/retry/lifecycle, parser call count,
  context isolation/redaction, IR-only `$ref` field retrieval, uncached search,
  and operation/evidence validation.

## Verification

- Focused App, context, retrieval, graph, operation, package, and MCP tests:
  - `uv run pytest -q tests/test_app_tool_context.py tests/test_tool_context.py tests/test_openapi_retrieval_agent.py tests/test_main_graph_mvp.py tests/test_operation_agent_mvp.py tests/test_operation_agent_policy.py tests/test_agent_package_boundaries.py tests/test_mcp_adapter.py tests/test_mcp_host.py`
  - Passed: 82 tests.
- `uv run pytest -q`
  - Passed: 145 tests; skipped: 1 opt-in live-model test.
- `uv run pytest -q tests/test_schemathesis_mcp_contract.py`
  - Passed: 1 real stdio contract test.
- `uv run python -m compileall -q restscope`
  - Passed.
- `git diff --check` and a scan for removed compatibility names in current
  code, tests, README, and active task records
  - Passed.
- No live LLM or real-target test was run. The implementation is preserved in
  a purpose-specific commit under the user's later authorization; it was not
  pushed.

## Remaining risks

- The IR is mutable by convention rather than structurally immutable.
- A baseline file can change after initialization before Schemathesis reads it.
- Live model quality and real target behavior remain intentionally unverified.

The 2026-07-23 unified-redaction decision supersedes the handler-error and
ToolContext-header masking recorded above. Target headers are now intentionally
visible; only exact configured THINK, FAST, and Phoenix API key values are
replaced by the shared App Redactor.
