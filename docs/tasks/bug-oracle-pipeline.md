# Bug Oracle Pipeline

Status: Completed

## Objective

Add a transport-connected response pipeline whose independent Observation,
Contract Monitor, Resource Monitor, and Bug Oracle stages exchange typed
App-lifetime annotations. A final Bug requires a deterministic candidate, an
isolated System Agent confirmation, and reproduction by one identical-request
Replay.

## Approved decisions

- Work directly on local `main`; do not push.
- Preserve the earlier Persistent Batch/Observation work in its own commit.
- Match the OpenAPI operation before entering the pipeline. Observation is the
  first pipeline stage for matched requests.
- Monitor validates the latest current Contract. Oracle validates the immutable
  initial Contract. Response decoding is shared.
- v1 checks valid-input 5xx, invalid-input 2xx, and response Schema mismatch.
- Every confirmed category for one Primary shares exactly one Replay. Replay
  passes through Observation and both Monitors, but cannot recursively confirm
  or Replay.
- Persist only final immutable Assessments and permanent Observations. Do not
  add a queue, pending workflow, generic annotation store, Tool, or UI.

## Non-goals

- Negative Generator implementation.
- Sequence-sensitive bug detection.
- Replay recovery, retries, or cross-App continuation.
- Oracle query Tools or Live Observer schema changes.

## Verification

- `uv run pytest -q`: 603 passed, 2 skipped.
- `uv run python -m compileall -q restscope tests`: passed.
- `uv run pytest -q tests/test_no_typing_any.py tests/test_workflow_package_boundaries.py tests/test_tools_catalog.py tests/test_schema_catalog.py`: 40 passed.
- `git diff --check`: passed.

## Remaining risk

No live target or online model was called. When the configured FAST model is
disabled or unavailable, deterministic candidates safely end as
`confirmation_failed` and cannot become Bugs.
