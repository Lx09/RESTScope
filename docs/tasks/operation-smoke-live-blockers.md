# Operation Smoke Live Blockers

Status: Implemented; post-live fixes unverified

## Objective

Remove the runtime blockers observed in the authorized Project API live run:
replace model-based Patch grouping with deterministic grouping, repair
hypothesis/Observation ownership, make request-body Patch samples projectable,
stop repeated diagnosis and Patch candidates, compact model-facing case
evidence, and attach the exact operation identity to HTTP Probe responses
processed by the API Behavior Monitor.

## Approved scope

- Keep grouping runtime-only and derive Groups from input overlap plus explicit
  same-request interaction notes.
- Allow a replacement hypothesis to inherit explicitly cited HTTP
  Observations and let the model judge whether owned Observations support its
  prediction.
- Fix request-body projection without adding a new date Generator.
- Stop a third identical hypothesis or Patch/error candidate.
- Use temperature zero for the remaining structured Operation Smoke roles and
  provide a compact, bounded view of all ten Smoke cases.
- Propagate Probe operation identity through an internal, model-invisible
  runtime context.
- Re-run the authorized Project API live test against port 37001 and inspect
  the new Phoenix trace on port 6006.

## Non-goals

- Modifying the Project API OpenAPI document.
- Changing public Operation Smoke request DTOs, the HTTP Tool JSON Schema, or
  Provider interfaces.
- Adding persistence, dependencies, custom date formats, or general Agent
  memory.
- Pushing a branch or creating a pull request.

## Decisions

- `PatchGroupPlanner` remains as the Operation Smoke facade but owns no LLM
  client or model.
- `confirmed` validation checks Observation existence and ownership only.
- Probe operation identity is exact and must not be reconstructed from a
  concrete URL that could collide with another OpenAPI route.
- A third identical Parameter Patch candidate returns
  `stalled_candidate`; a third identical material hypothesis defers the
  failure as `stalled_hypothesis`.
- Full execution evidence remains run-local; only the prompt projection is
  compacted.

## Verification

Local verification used the repository virtual environment directly because
the sandboxed `uv` cache/runtime could not be used:

- `../../.venv/bin/pytest -q tests/test_operation_smoke_plan_solve.py`:
  25 passed.
- `../../.venv/bin/pytest -q tests/test_parameter_patch_agent.py`:
  14 passed.
- `../../.venv/bin/pytest -q tests/test_operation_smoke_agent.py
  tests/test_supervisor_operation_smoke.py`: 16 passed.
- `../../.venv/bin/pytest -q tests/test_http_request_tool.py
  tests/test_resource_monitor_transport.py`: 49 passed.
- `../../.venv/bin/pytest -q tests/test_phoenix_tracing_contract.py
  tests/test_agent_package_boundaries.py`: 6 passed, 1 skipped.
- `../../.venv/bin/pytest -q`: 471 passed, 4 skipped.
- `../../.venv/bin/python -m compileall -q restscope tests`: passed.
- `git diff --check`: passed.

These local results preceded the final post-live retry correction described
below. At the user's direction, no test or live run was performed after that
last correction.

The fresh authorized live run against Project API port 37001 and Phoenix port
6006 passed all seven live test assertions in 2228.32 seconds. Its artifacts
are under:

`artifacts/project-swagger-smoke-e2e/project-swagger-smoke-20260727T010238Z-9712a1cf/`

The complete 1,555-span Phoenix trace was downloaded separately into that
directory as `phoenix-spans.json`. Observed results:

- No Grouping Agent or grouping LLM span exists. All 21 diagnoses that ended
  actionable produced at least one deterministic Patch Group.
- The run created 49 isolated Parameter Patch Agent runs. Forty-six validated;
  every validated run generated exactly ten local samples. Thirty-five of
  those runs sampled request-body inputs, including `body.commitDate`,
  `body.employee.hiredate`, `body.project.startDate`, and
  `body.project.endDate`.
- Two repeated Patch/error sequences stopped with `stalled_candidate`. Four
  repeated material hypotheses stopped with `stalled_hypothesis`.
- All 28 Probe HTTP calls were children of an investigation. All 28 associated
  Behavior Monitor spans had a non-null operation key, and their input,
  `restscope.operation.key` attribute, and output operation key agreed.
- The trace contains zero occurrences of the previous Observation ownership
  rejection, `Generated input has no request-shaped root`, the old Grouping
  output errors, or `operation_key=null`.
- Root-cause diagnosis, Parameter Patch, and Effect Validator LLM spans all
  used temperature zero. The compact evidence views stayed below their size
  limits; the largest diagnosis input recorded by tracing was 56,106 bytes.
Parameter Patch prompt input averaged 12,316 bytes versus 18,480 bytes in
the preceding live trace, while total run token use increased because this
run progressed through many more diagnosis, Patch, candidate, and effect
stages.

The live trace also showed one locally valid `body.commitDate` proposal repeat
unchanged through all 20 sample-review attempts. The first retry implementation
only counted proposals that failed deterministic validation, so a valid
proposal that the model refused to accept was not stopped. After the live run,
candidate signatures were extended to include successful proposals with an
empty current-error list, while non-proposal outputs reset consecutive
counting. A regression test for stopping the third identical sampled proposal
was added. Its pre-fix red state was observed; the user requested that no
post-fix tests be run.

## Live follow-up

The run also exposed a separate output-contract blocker that was not in the
original approved implementation scope. All 22 candidate batches reached the
Effect Validator, but all 44 initial/repair calls returned an unsupported
top-level shape such as `{"F1":"persisting"}`, `results`, or
`failure_statuses`. The runtime requires
`{"items":[{"item_id":...,"status":...}]}`. Its old prompt and repair guidance
did not state that shape, so all 34 initial failure assessments became
`unknown` with `Effect validation output was invalid`, and all 46 validated
Groups were rejected.

The user subsequently approved fixing this blocker. The Effect Validator now
uses one protocol generated from `PatchValidationDecision.model_fields` and
`PatchItemValidationDecision.model_fields`. Both initial and repair prompts
state the only legal `items` shape, enumerate every required item field, forbid
the invalid live wrapper shapes, include one DTO-validated complete example,
and limit `current_failure_refs` to the supplied candidate refs. Regression
coverage includes the exact live `{"F1":...,"F2":...}` failure followed by a
valid repaired response.

At the user's direction these post-live fixes were not executed under local or
live tests. No Parameter Patch can be claimed live-effect-accepted until a
future live run exercises the corrected Effect Validator protocol.
