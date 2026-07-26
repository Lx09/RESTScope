# Runtime Parameter Constraints

Status: In progress

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

Operation Smoke integration depends on the completed but currently uncommitted
`codex/fix-smoke-inconclusive-supervisor` work. It must be committed and merged
into local `main`, then incorporated here, before the Agent integration phase.
No action against that worktree is implied by this task.

## Current progress

- Design approved and recorded.
- Implementation plan recorded.
- Core `restscope.testing` implementation started with TDD.
- Operation Smoke integration not started.

## Verification

Baseline before implementation:

- `uv run pytest -q`: `375 passed, 16 skipped`.

Fresh feature and full-suite results will be added as work progresses.

## Risks

- The prerequisite Operation Smoke branch changes the Patch attribution and
  candidate-finalization contracts that this feature must extend.
- Finite candidate domains may be unable to satisfy a semantically valid
  relationship; that outcome must remain a pre-HTTP Patch validation failure,
  never an unconstrained fallback.
