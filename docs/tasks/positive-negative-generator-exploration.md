# Positive and Negative Generator Exploration

Status: Implemented and verified

## Objective

Give every request input positive and deterministic negative Generator
candidates, select them with separate App-lifetime e-greedy feedback, and let
the Main Agent explicitly pursue happy-path and exceptional testing.

## Approved decisions

- Implement directly on local `main`; do not create a worktree or branch.
- Keep selection, feedback, negative derivation, and Constraint connectivity in
  Request Generation rather than adding pass-through runtime layers.
- Initialize one positive candidate from the current behavior; allow a complete
  positive candidate list in Parameter Patch. Negative candidates come only
  from deterministic missing/scalar OpenAPI violations.
- Use epsilon 0.1. Happy 2xx rewards positive candidates. Every replay-confirmed
  Bug rewards the selected negative candidate, including repeated Bugs.
- Exceptional mode chooses negative mutation or ignored Constraint with equal
  probability. A negative mutation ignores the entire Constraint component
  connected to its input; if the remainder is unsatisfiable it retries with no
  Constraints.
- Statistics are in memory, revision-local, and affect only later Batches.
- Activate Main with the testing/failure-resolution method and one authorized
  apply-parameter-patch child Profile.

## Non-goals

- No resource-instance/state-level bandit, persistent learning state, scheduler,
  recovery state, array/object negative rules, third Constraint bandit, or live
  target verification.
- No Git stage, commit, merge, push, branch, or worktree lifecycle.

## Verification

- `uv run pytest -q tests/test_generator_exploration.py`: focused Generator,
  selection, Constraint-component, Patch-set, and reward checks passed.
- `uv run pytest -q`: 608 passed, 2 skipped before final cleanup; run again at
  handoff after all cleanup edits.
