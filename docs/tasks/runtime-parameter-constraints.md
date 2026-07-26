# Runtime Parameter Constraints

Status: Complete

## Objective

Add deterministic same-request parameter constraints to the existing
`restscope.testing` generation system, then integrate them with Operation
Smoke's failure-scoped Patch lifecycle.

## Approved scope

- Constraint contracts, evaluation, and finite-domain solving belong to
  `restscope.testing`.
- `constraints.py` owns the AST and semantics.
- `constraint_solver.py` owns candidate domains and bounded search.
- `generation.py` remains the single test-case generation entry point.
- Operation Smoke may infer constraints and keep accepted constraints for its
  current run, but it does not own their semantics.
- Constraint state is not persisted.

## Non-goals

- Cross-operation or cross-request relationships.
- Array-item occurrence constraints.
- A general SMT dependency.
- Constraint persistence or broader Agent memory.
- Changes to unconstrained `run_operation()` behavior.

## Dependency

The failure-scoped Operation Smoke prerequisite was verified, committed as
`d6aa25a`, merged into local `main`, and incorporated into this feature before
the Agent integration phase.

## Current progress

- Constraint AST, validation, normalization, classification, and total
  evaluation are implemented in `restscope.testing.constraints`.
- Deterministic finite-domain solving is implemented in
  `restscope.testing.constraint_solver`.
- Constrained test generation and Smoke-only zero-HTTP batch preflight are
  implemented without changing unconstrained `run_operation()`.
- Operation Smoke compiles semantic constraints, performs one repair after
  side-effect-free candidate preflight, validates constraints against a
  same-seed baseline, and keeps accepted constraints only for the current run.
- Constraint-only candidates do not create Generator catalog revisions.
- No constraint AST, assignment, solver state, or accepted constraint is
  persisted.

## Verification

Baseline before implementation:

- `uv run pytest -q`: `375 passed, 16 skipped`.

Fresh final verification:

- Focused testing, Operation Smoke, and package-boundary suite:
  `143 passed`.
- `uv run pytest -q`: `469 passed, 4 skipped`.
- `uv run --extra tracing pytest -q`: `469 passed, 4 skipped`.
- `uv run python -m compileall -q restscope`: passed.
- `git diff --check`: passed.

No live target, external LLM provider, or Phoenix deployment was exercised by
these offline tests.

## Remaining limits

- Finite candidate domains may be unable to satisfy a semantically valid
  relationship. That remains a pre-HTTP Patch validation failure and never
  falls back to unconstrained execution.
- Constraints cover same-request relationships only; cross-request,
  cross-operation, and repeated array-item relationships remain out of scope.
