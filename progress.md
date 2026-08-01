# Progress Log: Agent Tool Runtime Simplification

## Session: 2026-08-01

### Phase 0: Isolated workspace and task record

- **Status:** completed
- Loaded the project governance, TDD, planning, and deep-Module guidance.
- Confirmed pre-existing main-worktree changes and left them untouched.
- Created worktree `.worktrees/agent-tool-runtime` on branch
  `codex/agent-tool-runtime` from local `main`.
- Recorded the user-approved design and public test seams.

### Phase 1: Core Tool Module

- **Status:** completed
- Added the first public-interface test for duplicate tool names. RED failed
  because `AgentToolbox` did not exist, as expected.
- Added the smallest `AgentToolbox.register` implementation that keeps each
  specification and executable implementation together and rejects duplicate
  names.
- Confirmed the missing-implementation scenario RED, then made registration
  reject non-callable implementations immediately.
- Confirmed invalid arguments RED because execution did not yet exist. Added
  the approved direct `jsonschema` dependency and the smallest pre-execution
  validation path.
- Confirmed malformed success output RED, then validated the model-facing
  `structured` value against its declared output schema before constructing a
  successful `ToolResult`.
- Confirmed an unexpected exception escaped RED, then converted it to a stable
  `internal_tool_error` without returning the raw exception text.
- Confirmed the Agent specification seam RED, then exposed only this toolbox's
  specs in deterministic registration order.
- Confirmed batch execution RED, then added concurrent execution whose returned
  results remain in the model's original call order.
- Confirmed partial batch execution RED, then separated validation from
  execution so every call is checked before any implementation starts.
- Confirmed an owned tool could omit its output contract RED, then required an
  output schema for every RESTScope `local_function` registration.
- Confirmed an invalid JSON Schema registered RED, then made construction check
  both declared contracts before the tool becomes visible.
- Confirmed App redaction was absent RED, then bound `AgentToolbox` to the
  existing tracing runtime so every successful or failed model result is
  redacted at one final boundary. Unexpected exceptions are recorded only in a
  redacted internal trace event.
- Confirmed expected domain failures had no shared contract RED, then added
  `ToolFailure` so a tool can return one safe code, message, and optional
  bounded content without exposing unexpected exceptions.
- Added the approved direct `jsonschema` dependency and validated both local
  and MCP contracts at toolbox construction.

## Verification Log

| Command | Result |
|---|---|
| Not started | Pending first RED test |
| `uv run pytest -q tests/test_agent_tools.py::test_agent_toolbox_rejects_duplicate_tool_names` | RED: expected `ImportError` for missing `AgentToolbox` |
| `uv run pytest -q tests/test_agent_tools.py::test_agent_toolbox_rejects_a_missing_tool_implementation` | RED: registration incorrectly accepted `None` |
| `uv run pytest -q tests/test_agent_tools.py::test_agent_toolbox_rejects_invalid_arguments_before_execution` | RED: `AgentToolbox.execute` did not exist |
| `uv run pytest -q tests/test_agent_tools.py::test_agent_toolbox_rejects_success_output_that_breaks_its_schema` | RED: malformed structured output was reported as succeeded |
| `uv run pytest -q tests/test_agent_tools.py::test_agent_toolbox_hides_unexpected_exception_details` | RED: raw `RuntimeError` escaped execution |
| `uv run pytest -q tests/test_agent_tools.py::test_agent_toolbox_returns_only_its_registered_specs_in_order` | RED: the public specs Interface did not exist |
| `uv run pytest -q tests/test_agent_tools.py::test_agent_toolbox_executes_independent_calls_concurrently_in_call_order` | RED: the shared batch execution Interface did not exist |
| `uv run pytest -q tests/test_agent_tools.py::test_agent_toolbox_validates_a_whole_batch_before_any_call_runs` | RED: the valid call ran before another call's invalid arguments were known |
| `uv run pytest -q tests/test_agent_tools.py::test_agent_toolbox_requires_output_schema_for_restscope_tools` | RED: a local tool registered without an output schema |
| `uv run pytest -q tests/test_agent_tools.py::test_agent_toolbox_rejects_invalid_json_schemas_during_registration` | RED: a malformed input schema registered successfully |
| `uv run pytest -q tests/test_agent_tools.py::test_agent_toolbox_redacts_every_model_visible_success_value` | RED: the toolbox could not receive the App tracing/redaction runtime |
| `uv run pytest -q tests/test_agent_tools.py::test_agent_toolbox_returns_an_explicit_expected_failure` | RED: the expected-failure Interface did not exist |

### Phase 2: Scoped workflow tools

- **Status:** completed
- Added a Dedup public-seam RED test showing that two legal Catalog queries in
  one model output were incorrectly treated as a correction.
- Bound the current Catalog into a Dedup-owned `AgentToolbox` and routed the
  complete call group through shared batch execution.
- Added the scoped-OpenAPI RED scenario, then bound the current `OperationIR`
  into a zero-argument Dedup tool. The model can no longer supply or forge an
  operation key. A late-bound operation provider exposes only the current
  operation from the App runtime.
- Added a Solve public-seam RED test whose two independent Memory reads must
  overlap. Moved Memory reads into the shared toolbox, delayed all session
  bookkeeping until ordered results return, and bound Catalog, Patch, and HTTP
  implementations into the same Solve-owned toolbox.

### Phase 3: Capability and MCP cleanup

- **Status:** completed
- Moved the initialized target/OpenAPI context lifecycle onto
  `CapabilityRuntime` without retaining an App-wide executable registry.
- Kept the HTTP transport implementation reusable while binding it explicitly
  inside the current-operation Solve Probe.
- Removed the old Registry, Selector, Policy, Validator, Executor, and Resource
  Lookup wrapper modules and their public compatibility exports.
- Removed the unused ToolSpec risk, read-only, approval, and per-tool timeout
  declarations. MCP discovery now maps source contracts without deciding Agent
  availability.
- Migrated the HTTP behavior tests to a deliberately constructed Agent toolbox;
  the focused core, Dedup, Solve, and HTTP set now passes 64 tests.
- Removed the obsolete Catalog execution wrapper. Its bounded query semantics
  now raise an explicit `ToolFailure`, while the shared toolbox owns mechanical
  execution and final results.
- Made the shared toolbox the sole Probe tracing/redaction boundary and retained
  current-operation scoping and Test Case recording inside the Probe.
- Updated current README and reading-guide text; marked the corresponding
  sections of the older LLM design document as historical.
- Completed the full local and optional-tracing verification. No live LLM,
  target API, MCP process, or Phoenix service was called.

| Command | Result |
|---|---|
| `uv run pytest -q tests/test_failure_dedup_agent.py::test_dedup_executes_multiple_independent_tool_calls_in_one_output` | RED: `corrections == 1` because multiple calls were forbidden |
| `uv run pytest -q tests/test_failure_dedup_agent.py::test_dedup_openapi_tool_is_bound_to_the_current_operation` | RED: the tool schema still required a caller-selected operation key |
| `uv run pytest -q tests/test_failure_solver_agent.py::test_solve_executes_independent_memory_queries_concurrently_in_call_order` | RED: the first sequential lookup broke its barrier before the second started |
| `uv run python -m compileall -q restscope` | PASS |
| `uv run pytest -q tests/test_http_request_tool.py tests/test_agent_tools.py tests/test_failure_dedup_agent.py tests/test_failure_solver_agent.py` | PASS: 64 tests |
| `uv run --extra tracing pytest -q tests/test_observability_integration.py tests/test_smoke_tracking.py tests/test_phoenix_tracing_contract.py -m 'not phoenix_contract'` | PASS: 9 tests; 1 local-Phoenix contract deselected |
| `uv run pytest -q` | PASS: 516 tests; 6 environment-dependent tests skipped |
| `uv run python -m compileall -q restscope tests` | PASS |
| `git diff --check` | PASS |

## Implementation errors

- One combined patch included a stale `progress.md` context line and was
  rejected atomically. It was split into precise file-level patches before
  continuing.
- The first Dedup GREEN run found that the workflow package facade did not yet
  export the new Catalog query function. Added the intended internal public
  export before rerunning the behavior test.
- The first Solve GREEN run proved both barrier-controlled reads completed, but
  the new assertion incorrectly parsed Memory's intentionally Markdown tool
  result as JSON. Corrected the test to observe the established Markdown
  contract and ordered semantic handles.
- The first combined post-cleanup test run produced excessive output because
  32 HTTP tests still constructed the deleted global Executor. Replaced their
  test helper with an explicit Agent toolbox and reran the focused set cleanly.

## Error Log

| Timestamp | Error | Attempt | Resolution |
|---|---|---:|---|
| 2026-08-01 | Git feature ref creation was denied inside the filesystem sandbox | 1 | Used approved Git worktree creation access; branch and worktree were created successfully. |
| 2026-08-01 | `uv` could not initialize its cache inside the sandbox | 1 | Re-ran the focused test with approved cache access; the intended RED failure was observed. |
