# Generic Evidence Confidence

Status: Completed

## Objective

Provide one small in-memory model that can attach Beta-Bernoulli confidence to
any Python evidence payload. Callers can record equal-weight supporting or
opposing observations and immediately read the updated numeric confidence.

## Approved scope

- Add the public `restscope.evidence.Evidence[T]` interface in its own package
  so the root package remains limited to App composition and configuration.
- Start every instance with the fixed Beta(1,1) prior.
- Mutate alpha for supporting updates and beta for opposing updates.
- Calculate confidence as `alpha / (alpha + beta)`.
- Preserve the supplied payload by identity without inspecting or copying it.
- Keep confidence reads and updates atomic when threads share one instance.
- Add public-interface regression tests and fresh repository verification.

## Non-goals

- No persistence, history, deduplication, merging, weighting, decay, neutral
  observations, batch updates, confidence intervals, or calibrated truth
  probability claim.
- No Tool, Agent result, API Behavior Monitor, database, or dependency change.
- No merge, push, branch creation, or cleanup. The user later authorized one
  scoped local commit after implementation and verification.

## Decisions

- `confidence` returns only the current floating-point score; alpha, beta, and
  observation counts remain implementation details.
- `update(*, supports: bool)` changes the current instance and returns the new
  confidence. Non-boolean values are rejected before state changes.
- `data` cannot be rebound through the public interface. Mutable payloads may
  still be changed internally by their owner because the wrapper intentionally
  retains the original object rather than copying it.
- The user explicitly chose implementation in the current local `main`
  checkout instead of the project's usual feature worktree.

## Verification

Observed on 2026-08-11:

- `uv run pytest -q tests/test_evidence.py tests/test_no_typing_any.py
  tests/test_workflow_package_boundaries.py`: 30 passed.
- `uv run pytest -q`: 586 passed and 3 skipped.
- `uv run python -m compileall -q restscope tests`: passed.
- `git diff --check`: passed; a separate scan found no trailing whitespace in
  the new untracked production, test, or task-record files.
- The user subsequently authorized committing this scoped Evidence change on
  local `main`; no push or other external Git action is authorized.
