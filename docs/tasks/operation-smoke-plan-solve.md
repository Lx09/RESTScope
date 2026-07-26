# Operation Smoke Plan & Solve Diagnosis

Status: Implemented and verified; uncommitted

## Objective

Replace the narrow two-round Operation Smoke diagnosis with a bounded,
request-local Plan & Solve state machine that can gather new evidence before
forming one compatible Generator patch.

## Approved decisions

- THINK owns planning and investigation; FAST compiles all ready analyses into
  one joint patch.
- One diagnosis allows at most 20 valid plan decisions and 40 HTTP tool rounds.
  A tool round contains at most four serial requests and does not consume the
  decision budget.
- The initial plan cannot call tools. Later output is either HTTP tool calls or
  one complete PlanState update, never both.
- Every known failure must remain classified as ready, pending,
  non-parameter, or unplanned. `I*` plan items may be added, merged, split, or
  moved between ready and pending.
- HTTP probes may call only the current operation's method and a concrete path
  matching its frozen path template. Scope failures are rejected before
  transport.
- The same API Behavior Monitor processes probe responses. New HTTP failures
  receive new `F*` references and observations receive `O*` references.
- The final FAST result accounts for every ready item as covered or deferred.
  Each input can change at most once, and every covered item must have one of
  its affected inputs changed.
- A malformed plan or final patch gets one repair. Plan repair is free; final
  patch and its repair are outside the THINK decision budget.
- A decision-limit termination still patches ready work. All non-parameter
  failures return `no_parameter_issue`; unresolved work without an applicable
  patch returns `inconclusive`.

## Evidence and privacy boundary

- Model-facing inputs use semantic request paths, not persistent
  `input_node_id` values.
- Initial evidence contains batch status groups, `F*` failures, `C*` failed
  cases, actual generated values, omitted inputs, bounded response bodies,
  transport/monitor evidence, and a compact previous-candidate experiment
  summary.
- Each evidence item is limited to 64 KiB and the journal to 256 KiB. Truncated
  evidence remains structured and records its original byte size.
- Full OpenAPI Schema, Generator revision, internal node IDs, ToolContext
  Authorization/Cookie values, and observed pool values do not enter prompts.
- As in the project-wide redaction decision, only configured THINK, FAST, and
  Phoenix API key values are replaced in traces. Target evidence and model
  reasoning remain visible.
- Private response bodies, PlanState, EvidenceJournal, tool history, and
  reasoning are not persisted and do not enter LangGraph state or the public
  execution report.

## Implementation

- `operation_smoke.evidence` owns semantic input handles and bounded evidence.
- `operation_smoke.planning` owns model decisions and validated PlanState
  transitions.
- `operation_smoke.probe` scopes the existing global HTTP capability to the
  current operation.
- `OperationSmokeDiagnoser` runs the synchronous THINK/tool loop and the final
  FAST compiler without introducing a nested LangGraph or another Agent.
- `OperationTestingService.run_operation_for_smoke()` adds an internal
  App-lifetime result carrying failure response/monitor evidence while
  `run_operation()` and its public report remain unchanged.
- `OperationSmokeAgent` stages one candidate revision, reruns the complete
  batch with the same seed, then accepts or rolls back the candidate exactly as
  before.

## Non-goals

- Persisting plans, evidence journals, tool histories, reasoning, or raw
  responses.
- Changing the Generator Catalog schema, global HTTP tool contract, Supervisor
  scheduling, or API Behavior Monitor persistence.
- Live DeepSeek, target API, or Phoenix contract execution.

## Verification

Fresh offline verification:

- Operation Smoke, execution, Behavior Monitor, HTTP, LLM, and tracing
  regressions are included in the complete suites below.
- `uv run pytest -q`: `395 passed, 4 skipped`.
- `uv run --extra tracing pytest -q`: `395 passed, 4 skipped`.
- `uv run python -m compileall -q restscope`: passed.
- `git diff --check`: passed.

The approved plan named `tests/test_schemathesis_mcp_contract.py`, but the
current repository no longer contains that file or the Schemathesis testing
stack. The attempted invocation therefore reported “file or directory not
found”; the current local testing and HTTP contract suites passed as part of
the root runs.

No real DeepSeek request, target API request, Phoenix contract run, commit,
merge, push, or worktree cleanup was performed.
