# Resource Identifier Bounded Classification

Status: Implemented and locally verified; uncommitted

## Objective

Replace response-wide Resource Identifier classification with an IR-first,
resource-item-aware flow that uses deterministic rules for exact `id` fields
and a strictly bounded FAST fallback for semantic identifiers.

## Approved scope

- A root object is one resource instance.
- A root array, or each top-level array of objects in a wrapper response, is
  one resource group whose items are instances of the same resource.
- V1 does not classify nested objects inside a resource item as independent
  resources.
- Process at most the first 1000 collection items in stable response order.
- A resource item may contain at most 1000 recursive JSON scalar values.
  Oversized items are skipped with a warning while other items continue.
- Identifier candidates are immediate resource-item fields with observed or
  schema-declared string/integer types. Boolean, number/float, null, object,
  and array fields are excluded.
- Observed response fields precede IR-only fields in stable candidate order.
- An exact normalized field name `id` wins over all other candidates and does
  not require an LLM call.
- A schema-declared exact `id` must be observed with a usable value before its
  selector is persisted. If absent, report `expected_resource_id_missing`,
  write no identifier value or rule, and retry on a future 2xx response.
- Non-exact names ending in `id`, including `userId`, `project_id`, and `iid`,
  require FAST semantic selection.
- If no `id`-ending candidate exists, FAST may inspect other top-level
  string/integer candidates.
- Any LLM-backed selection examines at most 100 candidates in two stable
  batches of at most 50.
- Each resource group has a strict maximum of two real Provider calls,
  including structural repair. A repair consumes the second call and prevents
  examining a second candidate batch.
- The LLM receives operation method/path, the locally resolved canonical
  resource name, and bounded candidate metadata. It never receives actual
  identifier values, full responses, complete OpenAPI documents, selectors,
  database IDs, aliases, or Pool contents.
- The LLM returns only a supplied temporary candidate ID or `null`. Local code
  restores the selector and field name.
- A valid `null` result after the available batches produces `ignored` without
  persisting a negative rule or error. Future responses may retry.
- Invalid output may be repaired once within the two-call budget. A still
  invalid result records `resource_monitor_output_invalid` without a rule.
- A selected identifier must be observed at least once before the rule is
  persisted.
- Apply the learned selector to at most 1000 items. Persist valid typed,
  deduplicated identifiers even when some items omit the identifier; report
  the missing count and at most 20 item locations.

## Resource naming

Canonical resource names are resolved locally without an LLM:

1. Reuse a canonical name when a meaningful local alias exactly matches the
   persisted Resource Catalog.
2. Otherwise use the OpenAPI response item schema title/name when available.
3. Otherwise derive a singular name from the operation path's last
   non-template segment.

Generic wrapper names such as `collection`, `data`, `items`, and `results`
describe response location only and are not persisted as resource names or
aliases.

## Persistence

No database migration is required. Successful observations continue to update:

- `resources`
- `resource_aliases`
- `operation_resource_rules`
- `resource_identifiers`
- `resource_operation_usages`
- `resource_monitor_errors`

Legacy persisted `has_resource=false` rules are not treated as authoritative.
New negative decisions are not persisted, and a later positive observation may
replace a legacy negative rule for the same operation/group.

Raw responses, LLM reasoning, candidate batches, and evolved IR remain
unpersisted.

## Non-goals

- Nested-resource classification.
- Identifier ranking, embeddings, static synonym dictionaries, or actual-value
  prompts.
- More than two Provider calls per resource group.
- Database schema changes.
- Response Value Tracker behavior changes.
- OpenAPI Retrieval integration.
- Real target or external-model verification during implementation.
- GitHub CI/CD, push, PR creation, or automatic commit.

## Verification

Run:

```bash
uv run pytest -q tests/test_resource_monitor_agent.py
uv run pytest -q tests/test_resource_monitor_transport.py
uv run pytest -q tests/test_app_tool_context.py tests/test_operation_smoke_agent.py
uv run pytest -q
uv run python -m compileall -q restscope
git diff --check
```

No live target, external model, Phoenix live contract, or GitHub CI/CD action
is part of this task.

## Implemented result

- Collection responses are evaluated as resource-item groups. Nested objects
  stay attributes of the enclosing item in V1.
- Candidate construction filters types before applying the exact-`id` rule, so
  a boolean, number, or null field named `id` cannot hide a valid semantic
  string/integer candidate.
- Exact normalized `id` fields use deterministic local selection. Other
  `id`-suffix candidates and fallback scalar candidates use the minimal
  `ResourceIdentifierSelection` model contract.
- FAST receives at most two batches of 50 candidates, with two Provider calls
  total per resource group including repair.
- Successful and partially successful collection extraction persists typed,
  deduplicated identifier values through the existing Resource Catalog
  transaction. Negative classifications are not persisted.
- The repository can upgrade a legacy negative operation/group rule when later
  positive identifier evidence is observed.

## Verification results

Executed in the dedicated feature worktree:

```text
uv run pytest -q tests/test_resource_monitor_agent.py
29 passed

uv run pytest -q tests/test_resource_monitor_transport.py
10 passed

uv run pytest -q \
  tests/test_app_tool_context.py \
  tests/test_operation_smoke_agent.py \
  tests/test_supervisor_operation_smoke.py \
  tests/test_api_behavior_response_value.py
29 passed

uv run pytest -q
407 passed, 14 skipped

uv run python -m compileall -q restscope
passed

git diff --check
passed
```

The first full-suite attempt exposed only an uninitialized, Git-ignored
`services/schemathesis-mcp/.venv` in the new worktree. After synchronizing that
locked subproject environment, its real stdio contract test passed and the
complete suite passed. No production file was changed for that environment
issue.

Live target behavior, a real external FAST model, and Phoenix trace output
remain intentionally unverified. No GitHub CI/CD action was run or triggered.
