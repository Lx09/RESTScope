# Agent Tool Runtime Simplification

Status: Implemented and locally verified

## Objective

Replace the App-wide executable tool registry and role policy with a small
Agent-owned Tool Module. The Module registers complete tools, validates model
arguments before execution, validates successful outputs, converts errors,
applies the App redactor and tracing runtime, and supports deterministic
concurrent execution for Dedup and Solve.

## Approved scope

- Give each Agent only its explicitly registered tools.
- Scope shared OpenAPI and HTTP implementations to the current operation before
  model exposure.
- Combine registration, validation, execution, redaction, tracing, and
  concurrency behind one Interface.
- Require unique names and executable implementations; do not support
  replacement.
- Require input and output schema validation for RESTScope-owned tools.
- Return safe stable failures for unexpected exceptions without exposing their
  raw text to the model.
- Run a fully validated batch concurrently, retain model call order in results,
  and apply workflow-owned state changes afterward in that same order.
- Let Dedup return multiple independent tool calls without an additional
  numeric cap.
- Keep MCP Host as an isolated explicit integration.
- Remove Resource Lookup's model-tool wrapper while preserving the API Behavior
  Monitor's direct lookup capability and data.
- Remove the old tool registry, selector, policy, validator, executor, broad
  ToolContext injection, unused declarations, and compatibility exports.

## Non-goals

- No generic Agent reasoning loop or shared workflow policy.
- No effect/call-mode attributes, permission objects, new persistence, or
  compatibility aliases.
- No live LLM, target API, Phoenix, or external MCP calls.
- No commit, merge, push, or cleanup without separate authorization.

## Test seams

- Public Tool Module registration and execution Interface.
- `FailureDedupAgent.deduplicate`.
- Public Failure Solve investigation flow.
- Optional MCP construction Interface.
- Default App construction.

## Verification plan

- Focused Tool Module and Agent tests after each red/green slice.
- Capability, MCP, HTTP, ToolContext, package-boundary, tracing, and App
  regression suites.
- Full core and tracing test suites, Python compilation, residual-name checks,
  and `git diff --check`.

## Result

- Agents now construct one `AgentToolbox` containing only their explicitly
  bound specifications and implementations.
- The toolbox owns construction-time schema checks, whole-batch input
  validation, concurrent execution, ordered results, successful-output
  validation, safe error conversion, redaction, and tracing.
- Dedup uses current-operation OpenAPI and run-local Catalog tools. Solve uses
  run-local Memory and Catalog reads plus scoped Patch and HTTP tools; session
  bookkeeping is applied only after concurrent calls finish and in original
  call order.
- `CapabilityRuntime` retains shared target HTTP code, App context lifecycle,
  prompt-only skills, and an optional isolated MCP toolbox. It has no global
  RESTScope Agent tool registry or role policy.
- Resource Lookup remains available as a direct API Behavior Monitor catalog
  capability but is no longer a model tool.
- The old Registry, Selector, Policy, Validator, Executor, Resource Lookup
  wrapper, declarations, and compatibility exports were deleted.

## Fresh verification

- `uv run pytest -q -rs`: 516 passed, 6 skipped. The skipped cases require the
  evaluation dependency group, an explicitly enabled live GitLab/DeepSeek or
  Swagger Validator run, or the local Phoenix service.
- `uv run --extra tracing pytest -q tests/test_observability_integration.py tests/test_smoke_tracking.py tests/test_phoenix_tracing_contract.py -m 'not phoenix_contract'`:
  9 passed, 1 deliberately deselected local-Phoenix contract.
- `uv run python -m compileall -q restscope tests`: passed.
- `git diff --check`: passed.
- Production residual-name search found no reference to the deleted tool
  runtime Interfaces.

Live LLM, target API, external MCP process, and local Phoenix service behavior
were not exercised because this task did not authorize external actions.
