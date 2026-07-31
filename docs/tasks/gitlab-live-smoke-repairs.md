# GitLab Live Smoke Repairs

Status: In progress

## Objective

Repair the correctness, safety, and efficiency problems observed while smoke
testing the five GitLab project operations on 2026-07-30.

## Approved scope

- Keep rejected Failure Solve tool calls out of later provider requests.
- Show actual generated request inputs to Planner and Failure Solve without
  copying transport-owned credentials.
- Permit diagnostic HTTP probes only for GET, HEAD, and OPTIONS operations.
- Offer resource identifiers only to semantically related inputs and expose
  response-reference provenance in Agent prompts.
- Reject a changed `oneOf`/`anyOf` child unless the Patch exclusively selects
  that branch through every enclosing Variant.
- Skip identifier-model calls for acknowledgement-only response envelopes.
- Suppress HTTP/OpenAI library DEBUG logs while retaining RESTScope DEBUG logs.
- Verify locally, then rerun the five operations against the existing GitLab
  target and inspect Phoenix traces.

## Non-goals

- Add `multipart/form-data` generation or serialization.
- Change database schemas, public REST APIs, Dataset formats, or prompt
  persistence.
- Restore projects already marked for deletion.
- Commit, merge, or remove the branch/worktree without a separate checkpoint.

## Decisions and evidence

- The live DeepSeek 400 followed a rejected assistant response containing
  multiple tool calls without matching tool results.
- Public execution reports store Generator choices in
  `generated_test_case`; their prepared `request` summaries do not contain
  those original values.
- A DELETE Failure Solve probe produced additional target mutations, so
  mutating methods no longer receive the probe tool and forged calls fail
  closed.
- Project identifier pools were type-compatible with every serialized scalar
  Parameter. Matching now also requires a resource-related input name, with
  generic `{id}` resolved from the operation path.
- The live child-only integer Patch left the parent Variant weights at
  `[1, 1]`. A valid repair now requires explicit exclusive parent weights.
- GitLab DELETE acknowledgements contained only `message`; those responses
  cannot identify a reusable project.
- Phoenix already records effective prompts on `LLMClient.invoke` child spans.
  Dataset storage remains unchanged.

## Verification

- Focused red/green regressions:
  `uv run pytest -q tests/test_failure_solver_agent.py
  tests/test_smoke_plan_agent.py tests/test_operation_smoke_plan_solve.py
  tests/test_operation_smoke_references.py tests/test_parameter_patch_agent.py
  tests/test_resource_identifier_tracker.py tests/test_logging_config.py`
- Latest focused result: `70 passed in 1.22s`.
- Full suite: `472 passed, 17 skipped, 2 failed in 4.37s`.
- Both failures reproduce unchanged on local `main`:
  `test_object_cardinality_requires_a_generator_set_that_always_conforms`
  and `test_smoke_execution_applies_constraints_and_traces_only_the_count`.
  They are recorded as pre-existing and are outside this repair scope.
- Merged-main verification, live GitLab rerun, Phoenix inspection, and
  before/after project-state comparison remain pending.
