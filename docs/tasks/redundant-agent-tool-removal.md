# Redundant Agent Tool Removal

## Status

Implementation and local verification are complete on local `main`; no
staging, commit, push, or history rewrite was performed.

## Goal

Remove model-facing tools that merely repackage information already present at
the Agent boundary, while preserving every domain decision, validation rule,
sample, persistence effect, and external behavior.

## Approved test seams

- `ParameterPatchCoordinator.run`: Proposal and Review decisions remain valid
  through their owning Parameter Patch Module.
- `FailureDeduplicator.deduplicate`: one operation's Failures are grouped using
  the complete semantic-parameter authority supplied by the current Catalog.
- `FailureSolveAgent.start(...).advance()`: Solve can inspect exact input and
  response schemas and continue its investigation without listing inputs it
  already receives.

## Ordered slices

1. [x] Replace the Review-only `submit_parameter_patch_review` tool call with the
   existing JSON Schema response boundary; remove the redundant `accepted`
   input because `issues` already determines acceptance.
2. [x] Remove Failure Dedup's `openapi.list_inputs` call and put the already-known
   semantic handles in its bounded initial context.
3. [x] Remove Failure Solve's `openapi.list_inputs` call while retaining exact
   input-schema and response-field-schema tools.
4. [x] Remove `submit_parameter_patch_proposal` after retained trace evidence
   showed its strict transport produced fewer DTO-valid outputs than the
   existing JSON Schema path.

## Non-goals

- No changes to Patch compilation, Constraint solving, sampling, persistence,
  HTTP Probe behavior, or public Patch DTOs.
- No live LLM or target-system calls.
- No broad Agent-loop or provider refactor.

## Verification

- Focused public-seam tests for each vertical slice.
- Relevant Parameter Patch, Failure Dedup, Failure Solve, factory, and package
  boundary suites after integration.
- `uv run python -m compileall -q restscope tests`.
- `git diff --check`.

## Evidence log

- The Review tool accepts only `{accepted, issues}` and performs no domain
  action. Runtime code already normalizes acceptance to `not issues`, and the
  Agent already has a JSON Schema response fallback using the same DTO.
- Failure Dedup receives `catalog.valid_parameters` before its model call, but
  its prompt asks the model to fetch a second copy through
  `openapi.list_inputs`.
- Failure Solve receives all current semantic handles and repeats them in its
  scoped Memory and Patch tool schemas; the broad listing is not required to
  retrieve an exact input or response schema.
- The Proposal tool carries a recursive Patch/Constraint contract through a
  provider strict-schema route, but retained local traces contained 101 valid
  and 119 invalid strict DTO outputs, compared with 88 valid and 15 invalid
  JSON fallback outputs. The strict call frequently consumed one failed output
  before the same session succeeded through JSON Schema.
- The user explicitly retained the global `openapi.list_inputs` Capability.
  Only its redundant Dedup and Solve registrations were removed; its public
  specification, implementation, and focused Capability tests remain.

## Verification results

- Parameter Patch: 22 passed.
- Failure Dedup: 5 passed.
- Failure Solve: 29 passed.
- The obsolete untracked ten-operation GitLab live-test experiment was deleted;
  the tracked five-operation Projects test remains the sole live entrypoint.
- Full suite after deletion: 598 passed and 5 skipped; all 8 workflow
  package-boundary tests passed.
- Retained global OpenAPI Capability and context suites: 16 passed.
- `uv run python -m compileall -q restscope tests`: passed.
- `git diff --check`: passed.
