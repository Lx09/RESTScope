# Bug Oracle Pipeline

Status: Completed; verified on local `main`, uncommitted

## Objective

Maintain the transport-connected response pipeline while simplifying Bug Oracle
to one deterministic unexpected-status Check. A final Bug requires one identical
request Replay to reproduce the complete Primary trigger reason set.

## Approved decisions

- Work directly on local `main`; do not push.
- Preserve the earlier Persistent Batch/Observation work in its own commit.
- Match the OpenAPI operation before entering the pipeline. Observation is the
  first pipeline stage for matched requests.
- Contract Monitor alone evolves the latest current Contract. Response decoding
  remains shared; Oracle does not validate OpenAPI response schemas.
- Assessment schema v2 has one `unexpected_response_status` Check. Any 5xx adds
  `server_error`; invalid 2xx or 5xx adds `invalid_input_unexpected_status`.
- One Primary candidate receives exactly one Replay. Replay
  passes through Observation and both Monitors, but cannot recursively confirm
  or Replay.
- Replay must reproduce the exact complete reason set. Oracle uses no System
  Agent, prompt, session, or model reasoning.
- Persist only final immutable Assessments and permanent Observations. Do not
  add a queue, pending workflow, generic annotation store, Tool, or UI.

## Non-goals

- Sequence-sensitive bug detection.
- Replay recovery, retries, or cross-App continuation.
- Oracle query Tools or Live Observer schema changes.

## Earlier v1 verification

- `uv run pytest -q`: 603 passed, 2 skipped.
- `uv run python -m compileall -q restscope tests`: passed.
- `uv run pytest -q tests/test_no_typing_any.py tests/test_workflow_package_boundaries.py tests/test_tools_catalog.py tests/test_schema_catalog.py`: 40 passed.
- `git diff --check`: passed.

## Current implementation verification

- `uv run pytest -q`: 624 passed, 2 skipped.
- `uv run python -m compileall -q restscope tests`: passed.
- `uv run pytest -q tests/test_no_typing_any.py tests/test_workflow_package_boundaries.py tests/test_tools_catalog.py tests/test_schema_catalog.py tests/test_oracle_profiles.py`: 41 passed.
- `git diff --check`: passed.
- Ruff 0.16.2 was added as a reproducible dev dependency. The initial
  full-repository check found 671 issues. Safe mechanical fixes and reviewed
  semantic fixes now leave `uv run ruff check restscope tests` clean. Deliberate
  fail-open exception boundaries use local `noqa` markers, so the same rules
  continue to reject new unexplained broad exception handling elsewhere.

## Remaining risk

No live target was called. Resource identity may still use its existing FAST
System Agent, but Bug Oracle availability no longer depends on any model.
