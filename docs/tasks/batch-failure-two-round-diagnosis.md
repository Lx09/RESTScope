# Batch Failure Reporting and Two-Round Generator Diagnosis

Status: Implemented; local verification complete

## Objective

Replace failed-case replay and single-case probing in the planned Operation
Smoke flow with a deterministic batch failure report and two bounded FAST-model
calls:

1. locate suspicious input nodes from unique failure messages and concrete
   failed-case values;
2. propose generator patches using only the diagnosis and current generators
   for those nodes.

## Approved scope

- Preserve the existing batch cases and status statistics.
- Add unique, status-qualified failure messages with per-batch IDs and case
  associations.
- Read at most 1 MiB from each non-2xx response, retain at most 4 KiB per
  message, and report at most 100 unique messages.
- Use deterministic JSON/text/transport extraction without LLM summarization
  or semantic deduplication.
- Bound the first FAST request to 64 KiB, split between failure messages and
  failed-case concrete input values, and mark deterministic truncation.
- Keep the second FAST request limited to the first diagnosis and the current
  generators for selected input nodes.
- Do not replay or clone failed cases and do not issue single-case HTTP probes.
- Validate candidate generator revisions only by running another batch.
- Keep complete failure response bodies out of the public report and out of
  new persistence.

## Non-goals

- Semantic clustering, dynamic identifier removal, severity classification, or
  model-written failure summaries.
- GitHub CI/CD, live target traffic, real external model calls, push, merge, or
  worktree cleanup.
- Multi-round independent agent review.

## Baseline

```text
uv run pytest -q
367 passed, 12 skipped in 14.26s
```

The first sandboxed command could not access the existing uv cache. Re-running
the same command with approved uv cache access produced the baseline above.

## Verification

Implemented:

- `restscope.testing.run_operation` now builds a deterministic
  `BatchFailureReport` while preserving all existing per-case evidence and
  status counts.
- Non-2xx response bodies are bounded at 1 MiB and used only by failure
  reporting. Resource Monitor processing remains limited to 2xx responses.
- `restscope.agent.operation_smoke` owns the two FAST calls, prompt projection,
  validation, one repair per round, candidate lifecycle, batch-only
  verification, reference-pool waiting, and compensating rollback.
- `generator_config_revisions` persists immutable accepted, candidate,
  rejected, and rollback configurations. Migration `0004` backfills existing
  active configurations as accepted baselines.
- The default App and Supervisor use Operation Smoke and the local testing
  service. The legacy Schemathesis runner remains available only through
  explicit legacy dependency injection.
- Supervisor passes prior successful operation keys into each later Smoke
  request. OpenAPI Retrieval dependency resolution is not connected in this
  iteration.
- The default reference adapter resolves Resource Monitor identifiers. The
  `response_value` generator contract is supported and fails closed with
  `waiting`; a persistent generic response-value catalog remains future work.

Fresh verification:

```text
uv run pytest -q
390 passed, 12 skipped in 4.20s

uv run python -m compileall -q restscope
passed

services/schemathesis-mcp/.venv/bin/ruff check <changed production/test paths>
passed

git diff --check
passed
```

No GitHub CI/CD, live target traffic, external model call, commit, merge, push,
or worktree cleanup was performed.
