# Nest Parameter Patch Review

Status: Implemented and locally verified; uncommitted

## Objective

Make the small fresh-context Patch Reviewer an internal part of the Parameter
Patch Module instead of a peer Operation Smoke package.

## Approved scope

- Move the Review Agent, prompt, schemas, and strict decision tool under
  `restscope/operation_smoke/parameter_patch/review/`.
- Keep `ParameterPatchCoordinator` as the Reviewer's only production caller.
- Remove the top-level `parameter_patch_review` package and compatibility
  imports.
- Preserve model selection, fresh context, output budgeting, correction,
  strict fallback, trace names, and Patch behavior.
- Update package-boundary tests and current code-reading documentation.

## Decision

The Reviewer has an independent LLM decision and therefore keeps its own named
subpackage, but its Interface is an internal seam of Parameter Patch. It is not
exported through `parameter_patch.__init__` and is not an Operation Smoke peer
Module.

## Non-goals

- No prompt, schema, model-routing, Patch compilation, sampling, persistence,
  or live-provider behavior changes.
- No compatibility alias for the deleted top-level package.
- No real LLM, target API, Phoenix, or other external call.

## Verification

Fresh local verification on 2026-08-03:

- Parameter Patch, DeepSeek serialization, model selection, Operation Smoke
  wiring, and package-boundary tests: `84 passed`.
- Complete local suite: `550 passed, 18 skipped`.
- Python compilation for `restscope`, `tests`, and `evaluations`: passed.
- `git diff --check`: passed.

No real LLM, target API, Phoenix, or other external service was called.
