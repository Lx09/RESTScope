# Project Swagger Smoke E2E

Status: Completed; implementation and live verification preserved for local merge

## Approved goal

Exercise the default Supervisor and Operation Smoke path against
`assets/openapi/project_swagger.yaml` and the disposable API running on
`127.0.0.1:34985`. Every discovered operation must enter Operation Smoke and
execute at least one real HTTP batch. Preserve detailed execution tracking in
the local Phoenix service.

The target is approved for mutating `POST`, `PUT`, and `DELETE` requests. No
cleanup or restoration of target data is required.

## Acceptance boundary

- The full 67-operation asset is parsed and initializes the default runtime.
- The live run then scopes the App-lifetime IR operation index to a stable,
  method-diverse set of 10 operations (4 GET, 2 POST, 2 PUT, 2 DELETE), avoiding
  a new production filtering contract while satisfying the approved live
  coverage gate.
- At least 10 distinct operations have a Supervisor attempt, an Operation
  Smoke result, a real batch report, and generated HTTP request cases.
- Business-level HTTP failures are retained as evidence and may produce
  `completed_with_failures`.
- Unattempted or unsupported operations, global technical errors, missing
  batches, and incomplete Phoenix coverage fail the E2E test.
- The test uses the default dynamic runtime; it does not inject the legacy
  OperationTest runner, a static plan, or OpenAPI Retrieval.
- Phoenix records App, Supervisor, operation-attempt, Smoke, batch, case,
  behavior-monitor, diagnosis, and LLM boundaries without adding raw response
  bodies, pool values, or model reasoning to trace attributes.

## Implementation scope

- Add first-class spans around the existing orchestration boundaries.
- Add an explicit opt-in live pytest with deterministic coverage assertions,
  Phoenix pagination, and ignored local artifacts.
- Run the live test against port 34985 and local Phoenix, retain its report and
  tracking summary, and fix only RESTScope framework defects exposed by the
  run.
- Add focused regression tests before each production change.

## Non-goals

- Requiring every target operation to finish with 2xx success.
- Adding target-specific credentials, identifiers, or generator rules to
  production code.
- Persisting Supervisor queues, plans, or Agent intermediate state.
- Connecting OpenAPI Retrieval to Smoke.
- Running or changing GitHub CI/CD or pushing repository changes.

## Preservation

The main worktree originally contained a one-character indentation change in
`restscope/agent/operation_smoke/references.py`. Before this feature was
committed, the user explicitly cancelled that change; it is not included here.

The user subsequently authorized committing this worktree, merging it into
local `main`, and removing its feature worktree and branch. No push or GitHub
CI/CD action is authorized.

## Verification record

- Baseline:
  `uv run pytest -q tests/test_supervisor_operation_smoke.py
  tests/test_operation_smoke_agent.py tests/test_observability_integration.py
  tests/test_phoenix_tracing_contract.py -m 'not phoenix_contract'`
  → 13 passed, 1 skipped, 1 deselected.
- Tracking focused tests:
  `uv run pytest -q tests/test_smoke_tracking.py
  tests/test_supervisor_operation_smoke.py tests/test_operation_smoke_agent.py
  tests/test_testing_execution.py tests/test_resource_monitor_transport.py
  tests/test_observability_integration.py -m 'not phoenix_contract'`
  → 39 passed.
- LLM and observability regression:
  `uv run pytest -q tests/test_llm_mvp.py tests/test_llm_deepseek.py
  tests/test_observability_integration.py -m 'not phoenix_contract'`
  → 33 passed. The Phoenix contract exposed provider-private
  `reasoning_content` in LLM trace output; the trace projection now omits
  provider context without changing the DeepSeek runtime contract.
- Local Phoenix contract:
  `uv run pytest -q -s -m phoenix_contract
  tests/test_phoenix_tracing_contract.py`
  → 1 passed.
- RESTScope ↔ schemathesis-mcp stdio contract:
  `uv run pytest -q tests/test_schemathesis_mcp_contract.py`
  → 1 passed after prewarming the service's independent environment.
- Full local suite:
  `uv run pytest -q -m 'not phoenix_contract and not live_e2e'`
  → 431 passed, 3 skipped, 2 deselected.
- `uv run python -m compileall -q restscope
  tests/test_project_swagger_smoke_e2e_live.py tests/test_smoke_tracking.py`
  and `git diff --check` passed.
- Default live-test invocation:
  `uv run pytest -q tests/test_project_swagger_smoke_e2e_live.py`
  → 1 skipped with no target or model call.

## Live verification authorization

The configured real E2E sends target-derived unique failure messages and
generated failed input values to the configured FAST provider for Smoke
diagnosis. The user explicitly authorized that external transfer and clarified
that the live acceptance gate is at least 10 operations entering Smoke with
real request cases; those operations do not need to pass the success-rate
threshold.

```bash
RUN_PROJECT_SWAGGER_SMOKE_E2E=1 \
RESTSCOPE_E2E_ENV_FILE=/Users/lixin/Workplace/RESTScope/.env \
RESTSCOPE_E2E_ARTIFACT_DIR=/Users/lixin/Workplace/RESTScope/artifacts/project-swagger-smoke-e2e \
uv run pytest -q -s tests/test_project_swagger_smoke_e2e_live.py
```

Result:

- Passed in 123.30 seconds.
- Source asset operations: 67; selected live operations: 10.
- Supervisor attempts: 10; unattempted selected operations: 0.
- Smoke batches: 24; generated and executed HTTP cases: 240.
- Two operations met the 0.8 Smoke threshold. The other business-level
  failures were retained as `completed_with_failures`, as allowed by the
  approved coverage-first acceptance gate.
- One operation completed its HTTP batch but the DeepSeek patch remained
  structurally invalid after repair because it repeated two input nodes. This
  was recorded as `operation_error`; it does not invalidate the approved
  requirement that the operation enter Smoke and execute request cases.
- Phoenix project:
  `restscope-project-swagger-smoke-20260725T003753Z-ede72a9f`.
- Phoenix retained one trace with 610 spans:
  10 Supervisor attempts, 10 Smoke runs, 24 batches, 240 HTTP cases,
  240 Behavior Monitor observations, 18 diagnoses, 46 LLM calls, and 20
  Resource Identifier observations.
- Local evidence is retained under
  `/Users/lixin/Workplace/RESTScope/artifacts/project-swagger-smoke-e2e/project-swagger-smoke-20260725T003753Z-ede72a9f/`.

## Follow-up: task-focused live trace audit

On 2026-07-25 the user authorized another DeepSeek FAST/Phoenix run, including
target-derived failures and generated request values, normal model charges, and
potentially mutating requests to `127.0.0.1:34985`. The audit stopped after the
same ten-operation coverage gate and changed only trace-proven infrastructure
boundaries, not the Smoke diagnosis or candidate-evaluation algorithm.

The first fresh trace exposed two prompt-boundary defects:

- the Generator task card named intent kinds but omitted most required field
  names, while structural repair collapsed validation feedback to one generic
  sentence;
- observed response candidates returned up to 100 type-compatible fields
  without the exact-name/semantic selection already used by the Response Value
  tracker.

Later verification traces exposed two narrower cases: the supported
`formatted_value` values were missing, and the internal `request_body` control
node could be diagnosed and patched even though its Generator is fixed.
Regression tests were added before each production change. The resulting
boundary now:

- documents every Generator intent field and the supported uuid, date,
  date-time, and email formats in both the task and repair message;
- prefers backed exact-name response fields, uses the existing bounded `S*`
  semantic decision only as fallback, and returns at most ten response choices
  per input;
- exposes reference-backed Generators only to scalar inputs, with any observed
  scalar accepted for text-serialized OpenAPI parameters and type-strict values
  retained for JSON body fields;
- excludes the request-body control node from model aliases.

Final live verification:

- Run: `project-swagger-smoke-20260725T051015Z-ee716486`.
- Phoenix project:
  `restscope-project-swagger-smoke-20260725T051015Z-ee716486`.
- The E2E passed in 89.71 seconds. All ten operations entered Smoke.
- Phoenix retained one 694-span trace: 28 batches, 280 cases, 21 diagnoses, and
  42 LLM calls. All spans have `OK` status.
- Eighteen Generator calls required no structured-output repair. Their maximum
  trace input was 3,833 bytes, maximum observed-source count was 14, and no
  request-body control node appeared. The pre-fix fresh trace had five failed
  repair calls, 58–100 choices in every Generator task, and inputs up to about
  13.1 KiB.
- LLM inputs contain failed-case values but no internal input/reference IDs,
  selector, Pydantic Schema, config revision, Authorization header, or prepared
  request. Exact configured THINK, FAST, and Phoenix API key values were absent
  from the exported spans.
- Persisted monitors bind `employeeId`, `projectId`, and `commitDate` only to
  same-name selectors. Parameter monitors use an unset expected type so integer
  identifiers can be serialized into Spec-declared string paths. The final
  database contains one resource, eight identifiers, 21 observations, and no
  resource monitor errors.

Business outcomes remain intentionally report-only. Two list operations passed
without repair. The remaining operations produced non-2xx outcomes after one
to four batches; one `PUT /assignments/update` candidate produced a single 200
case but did not meet the 0.8 threshold. No production diagnosis or repair
algorithm was changed in response to those target outcomes.

On 2026-07-25 the user authorized committing these verified infrastructure
fixes, merging them into local `main`, and removing the feature worktree and
branch. No push was requested.
