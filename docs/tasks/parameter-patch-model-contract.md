# Parameter Patch Model Contract

## Status

The model contract was implemented, verified, committed, and merged. The
direct-title and cross-prompt trust-label follow-ups are also implemented and
freshly verified. Their local commit was explicitly authorized on 2026-08-04.

## Objective

Make the Parameter Patch response Schema describe exactly the Generator and
recursive Constraint language the model may submit. Keep the schema as the
machine-validated contract and add a compact, matching DSL summary to the Patch
Agent system prompt.

## Approved scope

- Expose only directly constructible Generator strategies in model output.
- Keep observed-value strategies available only through supplied `R*` aliases.
- Replace the untyped semantic Constraint dictionary with recursive typed DTOs
  that use semantic `input` handles.
- Preserve deterministic handle compilation, executable Constraint validation,
  sampling, Review, and correction behavior.
- Test through the exported `ParameterPatchSubmission` Interface and
  `ParameterPatchCoordinator.run`.

## Non-goals

- No Testing Module, persistence, HTTP, provider, or workflow behavior change.
- No compatibility aliases for incorrect or retired Patch fields.
- No live GitLab, DeepSeek, or Phoenix calls.
- No feature commit, merge, push, or worktree cleanup without later approval.

## Verification

- RED/GREEN tests for the response Schema and corrected proposal conversation.
- Parameter Patch, Operation Smoke evaluations, DeepSeek provider, and workflow
  package-boundary suites.
- Full local test suite, compileall, and `git diff --check`.

## Verification results

- RED: the constructible-Generator Schema test exposed four extra system-owned
  strategies; the recursive Schema test found no discriminator; the correction
  and DSL tests found their required guidance absent.
- `uv run pytest -q tests/test_parameter_patch_agent.py`: 55 passed.
- `uv run --group evaluation pytest -q tests/test_operation_smoke_evaluations.py`:
  13 passed.
- `uv run pytest -q tests/test_llm_deepseek.py tests/test_workflow_package_boundaries.py tests/test_smoke_tracking.py`:
  39 passed.
- `uv run --group evaluation pytest -q`: 628 passed and 5 skipped.
- `uv run python -m compileall -q restscope tests evaluations`: passed.
- `git diff --check`: passed.
- No GitLab, DeepSeek, or Phoenix live call was made.

## Direct-title follow-up

- Renamed each Patch prompt section and requirement field to state the action
  the model must take: produce the required outcome, inspect allowed-input
  state, preserve existing relationships, select available observed values,
  and preserve or avoid prior Patch results.
- Kept `UNTRUSTED` only on the four runtime-data sections. The required outcome,
  system DSL, and deterministic correction instructions remain trusted control
  content. The system prompt explains the marker once.
- RED: three prompt-rendering assertions observed the old labels and missing
  safety explanation.
- Focused Parameter Patch and evaluation command: 68 passed.
- Full suite with evaluation dependencies: 659 passed and 5 skipped.
- Compileall and `git diff --check`: passed.

## Cross-prompt trust-label follow-up

- Audited every direct model prompt in Failure Dedup, Failure Solve, Parameter
  Patch, Parameter Patch Review, identifier selection, and response-value
  source selection.
- `UNTRUSTED` now identifies every section containing runtime, OpenAPI, HTTP,
  Memory, tool, upstream-model, or generated-candidate data. It does not mark
  trusted static instructions or required replacement shapes.
- Replaced generic headings such as `TASK`, `ACTIVE CONSTRAINTS`, `Problems`,
  and `Correction Required` with titles that state how the model should use the
  enclosed facts. Rejection reasons and trusted replacement instructions now
  occupy separate sections.
- Corrected one Solve instruction typo without changing its decision protocol.
- RED: 11 focused tests failed only on the old headings, missing markers, or
  mixed rejection/replacement structure. GREEN: the same 11 tests passed.
- Prompt-related Agent, Context, Monitor, and evaluation suites: 161 passed.
- Full suite with evaluation dependencies: 660 passed and 5 skipped.
- No DTO, persistence, HTTP, tool, sampling, or runtime decision behavior was
  changed. No live call was made.

## Input Schema guidance follow-up

- `openapi.get_input_schema` now returns the selected Schema node's bounded
  `description` and singular `example` alongside its structural and validation
  facts when those annotations exist.
- The annotations remain inside `schema`; the tool still omits sibling fields
  and the complete Schema subtree. `openapi.get_response_field_schema` remains
  unchanged.
- Both annotations reuse the existing Schema-value size and depth limits before
  they can reach Failure Solve as untrusted tool evidence.
- RED: the exact-node Capability result and Failure Solve tool-message tests
  both showed the annotations were absent. GREEN: both passed after the change.
- OpenAPI lookup and Failure Solve suites: 48 passed.
- Full suite with evaluation dependencies: 672 passed and 5 skipped.
- No live call was made. This follow-up is included in the authorized local
  checkpoint before the next Failure Solve contract change.
