# Operation Smoke Diagnosis Contract Live Repair

Status: Complete

## Objective

Make the failure-investigation Prompt and its repair Prompt expose the exact
`FailureDecision` contract used by runtime validation, then update the Project
API live trace assertion for the failure-scoped investigation hierarchy.

The OpenAPI asset is deliberately unchanged. Inaccurate examples and parameter
types remain runtime evidence that RESTScope must diagnose and adapt to.

## Evidence

The authorized Project API run
`project-swagger-smoke-20260726T130403Z-9ea9d515` targeted
`127.0.0.1:37001` and exported 306 spans to its isolated Phoenix project.
All ten operations and 100 generated cases ran, but only two operations
satisfied the success threshold.

Eleven failure investigations made 33 diagnosis-model calls. Thirty-two
outputs failed `FailureDecision` validation and only one output was valid.
Consequently, the run produced no accepted hypothesis, HTTP Probe, actionable
failure, Patch Group, Parameter Patch Agent run, or candidate batch.

The common invalid outputs used `failure_ref` or `explanation`, omitted
`evidence_refs`, or returned `proposed_changes` as an object or scalar. The
initial Prompt named the decisions conceptually but did not show their exact
DTO fields. Repair turns returned validation errors without repeating a valid
complete contract.

The Phoenix spans were complete. Diagnosis LLM calls used the new hierarchy:

```text
OperationSmokeDiagnoser.diagnose
  -> OperationSmokeDiagnoser.investigate_failure
    -> LLMClient.invoke
```

The live test still required `LLMClient.invoke` to be a direct child of
`OperationSmokeDiagnoser.diagnose`, so it rejected the valid nested trace.

## Approved decisions

- `FailureDecision` remains strict and continues to forbid extra fields.
- An internal protocol renderer derives its allowed field names from the DTO
  and supplies DTO-validated minimal examples for each currently available
  action.
- The initial and repair Prompts share the same compact protocol.
- `confirmed` is shown only when the current hypothesis has an HTTP
  Observation reference.
- The Provider contract remains portable JSON output rather than native JSON
  Schema.
- Live acceptance requires at least one valid failure decision whenever the
  run contains investigations; it does not require a nondeterministic live
  Patch Agent success.
- The trace contract checks diagnosis, investigation, and LLM parentage
  separately.

## Verification

The implementation used test-driven development. The first focused run failed
the seven new contract and hierarchy tests before the protocol builder and
trace helper existed. After implementation:

- `uv run pytest -q tests/test_operation_smoke_plan_solve.py
  tests/test_project_swagger_smoke_e2e_live.py
  tests/test_phoenix_tracing_contract.py tests/test_agent_package_boundaries.py`
  passed with 34 tests and 2 skips.
- The final `uv run pytest -q`, after the tracing extra was available in the
  worktree environment, passed with 462 tests and 4 skips.
- `uv run python -m compileall -q restscope tests` passed.
- `git diff --check` passed.

The isolated worktree did not initially have the optional tracing dependency,
so the first live command stopped before testing the target API and reported
`No module named phoenix`. Rerunning the same test with
`uv run --extra tracing` supplied the declared optional dependency; this was
an environment correction rather than a product-code change.

The authorized live run
`project-swagger-smoke-20260726T134359Z-687034c8` then passed all seven test
checks in 463.19 seconds. It targeted `127.0.0.1:37001`, used the configured
DeepSeek provider, and exported 369 successful spans to the isolated Phoenix
project
`restscope-project-swagger-smoke-20260726T134359Z-687034c8`.

The repaired protocol changed diagnosis behavior materially:

| Metric | Before | After |
| --- | ---: | ---: |
| Failure investigations | 11 | 11 |
| Valid diagnosis outputs | 1 | 39 |
| Invalid diagnosis outputs | 32 | 9 |
| HTTP Probe calls | 0 | 9 |
| Patch Group runs | 0 | 1 |

All eight diagnosis spans and eleven investigation spans matched the report.
Every investigation was a direct child of its diagnosis and had at least one
direct LLM child. The complete live run remained in one trace and all 369 spans
had status `OK`.

The live run exposed two separate downstream limitations that were not changed
by this task:

- Four actionable diagnoses did not reach a Patch Agent because the grouping
  model returned the input task shape (or `grouped_solutions`) instead of the
  grouping DTO, repeated the same shape after its one repair, and exhausted
  grouping validation.
- The one successfully grouped task invoked `ParameterPatchAgent` for 20
  attempts. It repeated the same structurally valid date-format Patch, while
  local compilation rejected every attempt with `Generated input has no
  request-shaped root`. No local samples, candidate HTTP batch, or effect
  validation followed.

The remaining nine invalid diagnosis outputs were state-machine semantic
errors about confirming with the current hypothesis's Observation or replacing
an active hypothesis, rather than the former top-level DTO field drift.

Accordingly, the diagnosis protocol and trace hierarchy now have live
evidence, but Parameter Patch effectiveness does not. No candidate batch ran,
no Patch was finalized, and the OpenAPI asset was not modified.
