# Operation Smoke Plan & Solve Diagnosis

Status: Superseded by `operation-smoke-root-cause-parameter-patch.md`

> Superseded on 2026-07-26. The direct FAST joint-Patch compiler, global
> planning/tool budgets, four-call HTTP limit, and rule that the diagnoser must
> not invoke another Agent are historical. The current design diagnoses one
> failure at a time, then creates one fresh runtime `ParameterPatchAgent` per
> confirmed Patch Group. See
> `docs/superpowers/specs/2026-07-26-operation-smoke-root-cause-parameter-patch-design.md`.

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
  its affected inputs changed. Every change also names the covered `I*` items
  it serves.
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
  batch with the same seed, then invokes an independent THINK Patch Validation
  boundary and atomically accepts all, part, or none of the candidate changes.
- Patch Validation uses the prior item cause and solution, current `F*/C*`
  evidence, and per-input generated/omitted coverage. It cannot call tools,
  gets one repair, and does not consume planning-output budget.
- Accepted changes immediately form the parent of the next candidate. Partial
  finalization creates no rollback lifecycle revision and performs no extra
  stabilization batch.

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

## Live acceptance audit on port 37001

On 2026-07-26 the user authorized sending bounded Project API failure
evidence, generated request values, response bodies, and Plan & Solve prompts
to the configured DeepSeek THINK/FAST models. The user also authorized
potentially mutating requests to `127.0.0.1:37001` without restoration.

The final business run used
`assets/openapi/project_swagger.yaml`, selected the approved method-diverse ten
operations, and completed all ten Supervisor/Smoke attempts:

- Run: `project-swagger-smoke-20260726T005529Z-6c230f37`.
- Phoenix project:
  `restscope-project-swagger-smoke-20260726T005529Z-6c230f37`.
- Report: ten attempts, zero unattempted operations, 28 batches, and 280
  generated HTTP cases.
- Outcome: two collection GET operations satisfied the threshold; the report
  ended as `completed_with_failures`, with no graph-level error.
- Plan & Solve produced 21 valid planning outputs, four HTTP probe rounds, 18
  `patch_ready` results, one `no_parameter_issue`, and one `inconclusive`.
- Phoenix retained one 710-span trace containing 21 diagnosis spans, 51 LLM
  spans, four HTTP probe spans, and the matching case/monitor hierarchy. The
  updated contract assertion passes against the retained project.
- The trace used DeepSeek `deepseek-v4-pro` for 31 THINK calls and
  `deepseek-v4-flash` for 20 FAST calls. Exact configured API-key values were
  absent from the exported span payload.

The clearest parameter-level result was `POST /app/api/assignments`. Its first
batch contained six date-deserialization `400` responses and four `405`
responses. Plan & Solve used four bounded probes, identified
`body.commitDate` and `body.employee.hiredate`, and built one joint patch backed
by observed response values. The same-seed candidate batch contained ten
`405` responses and no `400` response, proving that the generated date values
removed the parsing failures and exposed the endpoint's non-parameter method
problem.

The audit also exposed four framework defects. Regression coverage and minimal
fixes are retained in `codex/fix-smoke-inconclusive-supervisor`:

- Supervisor's duplicated failure-kind contract rejected
  `diagnosis_inconclusive` and interrupted the graph.
- The model task card omitted the complete `ready` output contract, so sound
  diagnoses repeatedly failed DTO validation.
- The Phoenix live assertion assumed every Behavior Monitor span belonged to
  a generated case and rejected the legitimate monitor spans below HTTP
  probes.
- The improved task card assumed every operation had at least one configurable
  input and raised `StopIteration` for an input-free operation.

Fresh offline verification in that worktree:

- Complete suite: `396 passed, 4 skipped`.
- Focused migration, tracing, Plan & Solve, and Supervisor suite:
  `56 passed, 1 skipped`.
- `python -m compileall -q restscope`: passed.
- `git diff --check`: passed.
- Revalidation of the retained Phoenix project with the updated hierarchy
  assertion: passed with 710 spans and ten attempts.

The live pytest process itself ended on the now-fixed stale Phoenix assertion,
so this is not recorded as a green fresh live-test invocation. No second
DeepSeek/target run was made during the acceptance audit.

The live audit exposed a behavior limitation in the then-current code:
candidate acceptance depended only on the 2xx success threshold. The date patch
above was therefore rejected after it changed `400` failures into the
endpoint's `405` responses, and multi-parameter path fixes did not accumulate.
This limitation is superseded by the approved Patch Validation amendment:
below the threshold, THINK classifies each planned item as resolved,
persisting, or unknown; resolved-item changes become accepted immediately and
the next candidate is staged from that cumulative configuration. A candidate
that reaches the 2xx threshold still wins and is accepted in full.

The amendment keeps PlanState, failure evidence, response bodies, and model
reasoning ephemeral. Revision evaluation stores only batch metrics, validation
status, and accepted/rejected change counts. It adds no database columns or
migration.

Fresh offline verification of the amendment:

- `uv run pytest -q`: `409 passed, 4 skipped`.
- `uv run --extra tracing pytest -q`: `409 passed, 4 skipped`.
- Focused Operation Smoke, revision-history, LLM, tracing, Supervisor, and
  package-boundary tests passed before the complete suites.
- `uv run --extra tracing python -m compileall -q restscope`: passed.
- `git diff --check`: passed.

The repository still has no `tests/test_schemathesis_mcp_contract.py`; no
Schemathesis contract result is claimed. Verification used stubs, mock
transport, SQLite transaction tests, and in-memory tracing only. It made no
DeepSeek, target API, or Phoenix contract request.

The audit fixes and cumulative-merge amendment remain uncommitted in
`codex/fix-smoke-inconclusive-supervisor`. No new DeepSeek, target API, or
Phoenix contract run was made for the amendment. No commit, merge, push, or
worktree cleanup was performed.
