# Parameter Patch Model Contract

## Status

Implemented and freshly verified. Local commit, merge, and cleanup were
explicitly authorized on 2026-08-04.

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
