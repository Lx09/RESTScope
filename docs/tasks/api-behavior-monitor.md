# API Behavior Monitor

> Superseded navigation note (2026-08-12): this record describes an earlier
> Monitor and database layout. The current implementation uses the unified
> `APIBehaviorCatalog`, the nine-table baseline, and flat `contract_monitor.py`,
> `resource_monitor.py`, and `resource_identity.py` owners. Preserve this file
> as implementation history rather than a current code map.

Status: Completed

## Approved scope

Rename and expand Resource Monitor into the independent
`restscope.agent.api_behavior_monitor` Agent package. The new Agent coordinates:

- deterministic first-observation response-contract checks that directly update
  the current in-memory `OpenAPISpecIR`;
- the existing resource-identifier classification, persistence, and lookup
  behavior;
- persistent response-value monitors whose sources are selected from the latest
  IR and whose values are extracted from later successful JSON responses.

This is an incremental change. Existing resource-identifier behavior,
`ResponseValueGenerator`, and Operation Smoke's empty-reference `waiting`
behavior are reused rather than reimplemented.

## Decisions

- A response-contract observation is keyed by operation key, exact HTTP status,
  and normalized media type.
- Exact status definitions are materialized from an exact, class wildcard, or
  `default` baseline, in that order. Wildcard and default definitions are not
  overwritten.
- Runtime evidence may add statuses, media types, optional fields, and wider
  type unions. It never deletes fields, narrows types, or infers enums.
- Empty, text, and binary responses complete the first check. Truncated or
  invalid JSON remains pending and is retried by the next matching response.
- Response-contract state, IR mutations, and change summaries are App-lifetime
  only. No IR snapshot or first-observation registry is persisted.
- Resource identifiers and response values use the App database. Full response
  bodies, LLM reasoning, and Agent loop state are not persisted.
- Response-value source selection is IR-first. Exact normalized name and type
  matches are deterministic; a bounded FAST semantic choice is allowed only
  when deterministic evidence is insufficient.
- Resource identifiers and response values are extracted only from valid 2xx
  JSON responses. Failed responses never become reusable input values.
- Existing declared response headers may be inspected, but runtime-only or
  unknown headers are not added to IR.
- No request replay, external API call, GitHub CI/CD, or cross-process IR
  recovery is introduced.

## Verification

Implemented behavior:

- `restscope.agent.api_behavior_monitor` now owns response-contract,
  Resource Identifier, and Response Value tracking.
- Every first operation/exact-status/normalized-media observation checks and
  conservatively updates the current App's IR. Invalid or truncated JSON is
  retried; checked state and evolved IR are process-local.
- The previous Resource Monitor implementation was moved intact into the
  Resource Identifier subcomponent, with its tool name and database tables
  preserved.
- Response Value monitor registrations, IR-derived sources, and deduplicated
  typed scalar values are persisted in migration `0005`.
- Operation Smoke replaces model-proposed response-value names with a stable
  locally generated name, registers the monitor, and keeps the existing
  empty-pool `waiting` behavior.
- The raw HTTP tool and batch runner share one monitored transport. Their
  results expose `response_validation` and bounded API Behavior Monitor
  warnings without replacing the original HTTP response.

Fresh verification after the final production-code edit:

```text
uv run pytest -q \
  tests/test_api_behavior_contract_tracker.py \
  tests/test_api_behavior_response_value.py \
  tests/test_api_behavior_transport.py \
  tests/test_resource_monitor_agent.py \
  tests/test_resource_catalog.py \
  tests/test_resource_monitor_transport.py \
  tests/test_operation_smoke_agent.py \
  tests/test_testing_migration.py \
  tests/test_agent_package_boundaries.py
# 78 passed

uv run pytest -q
# 403 passed, 12 skipped

uv run python -m compileall -q restscope
# passed

git diff --check
# passed
```

## Remaining boundaries

The empty-pool `waiting` behavior described above was superseded by
`docs/tasks/pool-gated-smoke-retries.md`. The Monitor now persists bounded
scalar observation history for late backfill, while Smoke creates
reference-backed Generators only from already non-empty pools.

- No real target, external network, or live FAST-model request was executed.
- There is intentionally no cross-process recovery for evolved IR or the
  first-observation registry.
- Git preservation and local integration are recorded by repository history.
  No push, external call, or CI/CD execution was part of this task.
