# Failure Solve Three-Field Terminal Schema

**Status:** Implemented and locally verified; delivery in progress
**Approved:** 2026-08-03

## Objective

Make Failure Solve terminal output a small, flat decision containing only
`action`, `candidate_ref`, and `reason`. Keep multiple validated Patch
candidates, then derive applied and runtime-conflict memory facts from the
selected candidate rather than asking the Solve model to repeat them.

## Approved behavior

- `apply_patch` selects a current-session `P*` candidate and ignores terminal
  reason.
- `no_patch` ignores candidate reference and requires a non-blank reason.
- Tool calls naturally continue investigation; checkpoint, continue,
  next-step, and model-selected conflict are removed.
- Applied/conflict root cause and Parameter attribution come from the selected
  reviewed candidate. No-patch stores only its reason.
- Solve Attempt storage uses one non-empty reason, nullable root cause, and the
  existing Parameter link table. Legacy explanation columns are removed from
  the fresh-database baseline without a compatibility migration.

## Non-goals

- Do not change Generator or Constraint execution, Patch/Review strict tool
  calling, public selected-candidate reporting, or real Batch validation.
- Do not call DeepSeek, GitLab, Phoenix, or a target API.
- Do not commit, merge, push, or clean up Git state without later explicit
  authorization.

## Verification plan

Run the focused Failure Solve, Operation Smoke, Memory Repository, schema
catalog, evaluation, DeepSeek provider, and package-boundary tests, followed by
Python compileall and `git diff --check`.

## Verification results

- Focused runtime matrix: 111 passed.
- Developer evaluation suite: 13 passed using scripted local clients.
- Python compileall: passed for `restscope`, `evaluations`, and `tests`.
- `git diff --check`: passed.
- No live model, GitLab, Phoenix, or target API call was made.

The user authorized committing this implementation, merging it into local
`main`, and cleaning up its worktree and branch. Push remains unauthorized.
