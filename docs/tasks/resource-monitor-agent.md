# Resource Monitor Agent

Status: Superseded by `docs/tasks/api-behavior-monitor.md`

This record remains the historical source for the Resource Identifier
subcomponent. The approved API Behavior Monitor work moves that implementation
under a broader Agent package and adds response-contract and response-value
tracking without discarding the behavior documented below.

## Objective

Learn reusable resource identifiers from successful API operation responses,
record the resource and identifier names used across operations, and expose a
read-only lookup tool for later operation input assignment.

## Approved scope

- Implement the Agent as the independent
  `restscope/agent/resource_monitor/` package.
- Observe 2xx JSON responses synchronously inside the current App lifecycle.
- Persist only resource names and aliases, learned identifier selectors,
  typed identifier values, latest per-operation usage, and latest monitor
  errors in the App-owned single-API database.
- Use an exact `id` heuristic first. Batch unresolved top-level response groups
  into one configured FAST-model call, with at most one repair call.
- Give the FAST model only bounded semantic choices identified by ephemeral
  group and candidate IDs. Keep internal executable selector strings,
  identifier values, database IDs, and model-generated aliases outside the
  model contract.
- Reuse the learned rule for later responses. If an identifier that was
  previously observed is missing, report `expected_resource_id_missing`
  without relearning.
- `OperationTestingService` supplies its already-known `operation_key`.
  `restscope.http.request` keeps its open-world input contract; the observation
  adapter deterministically matches method and concrete path against the
  current OpenAPI IR.
- Resource Monitor receives only a resolved observation. It does not initialize,
  retain, or receive the full OpenAPI IR.
- Register `restscope.resource.lookup` explicitly as a read-only local tool.

## Non-goals

- No automatic generator assignment or test scheduling.
- No raw response, test plan, scheduler queue, LLM reasoning, or general Agent
  memory persistence.
- No resource lifecycle inference; identifiers observed through DELETE remain
  queryable.
- No background queue, worker, retries, multi-schema catalog, Schemathesis/MCP
  change, live target call, or automatically executed real-model verification.
- The opt-in DeepSeek FAST acceptance test remains skipped unless
  `RUN_RESOURCE_MONITOR_LIVE=1` is explicitly set.
- No CI/CD execution, commit, merge, push, or worktree cleanup without separate
  authorization.

## Decisions

- One response is divided into the root group and direct object/array child
  groups. Each group can describe at most one primary resource.
- Exact identifier matching ignores case and separators only. Semantic aliases
  such as `commitId` and `sha` require the FAST model.
- Existing resource aliases are resolved locally before classification. The
  model may select or propose a canonical resource name, but it cannot generate
  aliases; only the response-derived resource hint is persisted as an observed
  alias.
- Identifier values are non-empty strings or integers; booleans, floats,
  containers, nulls, and empty strings are ignored.
- GET, HEAD, and OPTIONS are reads. POST, PUT, PATCH, and DELETE are writes.
- The same identifier and operation rule retain only their latest occurrence.
- Monitor failures do not replace the original HTTP result. They produce a
  bounded structured warning and a latest-error record.
- Response monitoring is independently capped at 1 MiB. A single observation
  is also bounded to 50 groups, 500 collected fields, 1,000 scalar values, 100
  values per field, 4,096 bytes per identifier, 20 model candidates per group,
  and 100 model candidates per batch. Budget exhaustion fails closed without
  partial resource writes.
- FAST input contains only method/path, canonical resource names, response
  locations, resource-name hints, and bounded candidate semantics. Candidate
  descriptions are capped at 200 characters and schema formats at 200
  characters. The prompt intentionally includes semantic response locations
  and group-relative field paths, but never serializes actual values or the
  internal executable selector field.
- FAST output contains only `group_id`, `represents_resource`, an optional
  canonical resource name, and an optional candidate ID. The system maps the
  selected IDs back to the private selector, field name, and observed values.
- Model output must cover every supplied group exactly once, use strict JSON
  booleans, preserve locked candidates and matched canonical names, and omit
  resource fields for non-resources. Validation errors use ephemeral IDs and
  one bounded repair call is allowed.
- Existing resource context is loaded with bounded aliases, converted once
  into an in-memory alias-to-canonical map, and reused for the observation.
  Repository loading uses one resource query and one per-resource-bounded alias
  query instead of per-resource or per-group lookups.
- Groups without a supported identifier candidate are cached as non-resources
  without an LLM call. JSON property names containing selector-reserved `.`,
  `[` or `]` fail closed in this MVP rather than creating an unrecoverable
  persisted selector.
- Resource lookup applies SQL count/limit for identifiers, uses stable
  tie-breaking, keeps per-operation resource aliases, and filters operation
  usage by ID only when `id_value` is explicitly supplied.

## Verification results

Fresh commands after the prompt-contract optimization:

```bash
uv run pytest -q tests/test_resource_monitor_agent.py tests/test_resource_catalog.py \
  tests/test_resource_monitor_transport.py tests/test_resource_monitor_agent_live.py
# 54 passed, 1 skipped

uv run pytest -q
# 367 passed, 11 skipped

uv run python -m compileall -q restscope tests/test_resource_monitor_agent_live.py
# passed

git diff --check
# passed
```

Baseline before implementation:

- `uv run pytest -q`: 309 passed, 10 skipped.

The prompt implementation and opt-in live test each passed separate
specification and code-quality reviews. No Critical or Important findings
remain.

## Remaining risks and unverified behavior

- The deterministic raw-HTTP operation matcher must reject ambiguous template
  matches without contaminating the catalog; this is covered locally with
  parser and transport tests but has not been exercised against a live target.
- Synchronous FAST-model classification adds latency to the first successful
  response for an unresolved operation group.
- Property names containing `.`, `[` or `]` and schema formats longer than 200
  characters now return `resource_monitor_evidence_limit_exceeded`; supporting
  those names would require a future selector-format decision.
- The DeepSeek FAST acceptance test is available with
  `RUN_RESOURCE_MONITOR_LIVE=1`, but real FAST-model classification was not
  executed. Live target traffic, external network calls, and GitHub CI/CD were
  intentionally not run.
- The implementation was locally committed and approved for merge into `main`;
  no push, live target traffic, external model call, or CI/CD execution was
  performed.
