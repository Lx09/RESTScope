# Pool-Gated Generator and Non-Interrupting Smoke Retries

Status: Verified, uncommitted

## Objective

Make reference-backed generators selectable only from non-empty persistent
pools, retain bounded scalar evidence for late Response Value monitor
registration, and let Supervisor continue across operation-scoped failures with
bounded retry rounds.

## Approved decisions

- Operation Smoke never returns `waiting`.
- Smoke keeps its bounded internal feedback loop; Supervisor may invoke the
  same operation at most three times by default after other operations have had
  a chance to add global evidence.
- Each operation retains the latest 100 valid, non-truncated 2xx JSON
  observations as flattened typed scalar evidence. Full response bodies are
  not persisted.
- All scalar fields are eligible for persistence, including fields with
  sensitive-looking names. This is an explicitly accepted risk.
- OpenAPI Retrieval remains an independent, explicitly registered tool.
- Runtime IR evolution remains response-only and does not resynchronize
  persisted request generator snapshots.
- Operation-scoped failures do not stop the remaining queue. A completed run
  with final operation failures reports `failed/completed_with_failures`;
  shared-runtime failures report `errored/technical_error`.

## Non-goals

- Persisting Supervisor queues, retry state, Agent state, complete responses,
  request bodies, or evolved IR snapshots.
- Connecting OpenAPI Retrieval to Smoke, monitor registration, or scheduling.
- Rebuilding request generators after response-contract evolution.
- Running real targets, external models, GitHub CI/CD, or multi-agent review.

## Verification

Implemented:

- migration `0006_create_response_observation_history` stores the latest 100
  valid 2xx JSON scalar observations per operation without full bodies;
- late Response Value preview and registration use current IR sources and
  persisted history, with registration, source persistence, and pool backfill
  sharing one transaction;
- FAST receives only non-empty reference option metadata and must select an
  option ID; actual values and model-invented reference names are rejected;
- Operation Smoke returns `passed`, `retry`, `unsupported`, or `errored` and
  has no waiting path;
- Supervisor retries local failures in later rounds up to
  `max_operation_attempts`, continues remaining operations, and reports
  `completed_with_failures` when appropriate.

Fresh final verification:

```text
uv run pytest -q \
  tests/test_api_behavior_response_value.py \
  tests/test_operation_smoke_diagnosis.py \
  tests/test_operation_smoke_agent.py \
  tests/test_supervisor_operation_smoke.py \
  tests/test_main_graph_mvp.py \
  tests/test_testing_migration.py \
  tests/test_app_database_bootstrap.py \
  tests/test_schema_catalog.py
# 95 passed

uv run pytest -q
# 414 passed, 12 skipped

uv run python -m compileall -q restscope
# passed

git diff --check
# passed
```

The first full-suite run found three expected table-whitelist assertions that
did not yet include the two new migration tables. Those assertions were
updated before the final passing run.

No real target, external model, external network, GitHub CI/CD, Subagent,
commit, merge, push, or worktree cleanup was run. Commit and integration remain
subject to separate user authorization.
