# Persistent Batch and Test Case Results

Status: Implemented and verified; unstaged

## Objective

Persist every executed request result for a matched OpenAPI operation, group
generated cases under a durable Batch, and expose bounded read-only Agent Tools
for Batch summaries and individual Test Case evidence.

## Approved scope

- Add `batches` with a structured summary.
- Expand `observations` to permanent HTTP and transport results with optional
  Batch identity and stable Case order.
- Keep only complete valid 2xx JSON eligible for response-value and resource
  learning.
- Add `test_case.get_batch_results` and `test_case.get`; extend
  `test_case.run_batch` with its Batch identity and persistence warnings.
- Register and bind the new Tools without granting them to a production Profile.
- Update the fresh-database baseline only; no compatibility migration or data
  backfill is required.

## Non-goals

- No resumable scheduler, persisted Agent state, Test Case planning registry, or
  automatic recovery from a `running` Batch.
- No Profile capability changes, Git commit, merge, push, or branch lifecycle.

## Decisions

- `observation_id` is the Test Case ID.
- HTTP and transport are explicit outcome kinds; transport has no status code.
- All observations are retained permanently.
- Database response headers are complete. Agent Tool output replaces sensitive
  values with `[REDACTED]`.
- Tool body output is a fixed 16 KiB prefix with size and truncation metadata.
- Observation persistence failure does not stop later Batch cases; the Batch
  summary reports missing records and safe warnings.

## Verification

- Focused persistence, Monitor, Batch, Tool, Catalog, production-binding, and
  learning-consumer regressions: `80 passed`.
- Batch degradation and Test Case body-format regressions: `14 passed`.
- Final full suite: `uv run pytest -q` → `590 passed, 2 skipped`.
- `uv run python -m compileall -q restscope tests`, the precise
  `tests/test_no_typing_any.py` guard, and `git diff --check` all passed.
- No staging, commit, push, branch, or worktree operation was performed.
