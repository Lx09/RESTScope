# Resource Identifier Bounded Classification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use
> `superpowers:executing-plans` for inline execution. Project rules prohibit
> delegated implementation and multi-round independent review for this task.

**Goal:** Make Resource Identifier monitoring item-aware, deterministic for
exact `id`, and bounded to two FAST calls for semantic selection.

**Architecture:** Keep the public API Behavior Monitor and database schema
unchanged. Refactor the private evidence builder and selector inside
`resource_identifier.py`, reduce the internal LLM output to a candidate choice,
and preserve successful resource facts through the existing `ResourceCatalog`
transaction boundary.

**Tech Stack:** Python 3.11, Pydantic v2, SQLAlchemy, pytest, existing RESTScope
LLM and OpenAPI IR types.

---

### Task 1: Lock the new resource-item evidence boundary

**Files:**
- Modify: `tests/test_resource_monitor_agent.py`
- Modify: `restscope/agent/api_behavior_monitor/resource_identifier.py`

- [ ] Add failing tests showing a wrapped collection creates one group at
  `$.collection[]`, nested objects do not create extra groups, an exact `id`
  uses no LLM, and `collection` is not persisted as the resource name.
- [ ] Add a failing test showing response-field order is retained and IR-only
  fields are appended.
- [ ] Run the new tests and confirm failures against the response-wide current
  implementation.
- [ ] Replace `_build_groups` and `_collect_fields` with item-aware collection:
  inspect root objects directly; inspect at most 1000 root/top-level collection
  items; collect immediate scalar fields only; recursively count at most 1000
  scalar values per item.
- [ ] Merge schema evidence only when its selector is an immediate field of an
  existing resource group.
- [ ] Run the focused evidence tests and retain existing selector-safety
  failures.

### Task 2: Resolve resource names locally

**Files:**
- Modify: `restscope/agent/api_behavior_monitor/agent.py`
- Modify: `restscope/agent/api_behavior_monitor/resource_identifier.py`
- Modify: `tests/test_resource_monitor_agent.py`
- Modify: `tests/test_resource_monitor_transport.py`

- [ ] Add failing tests for persisted alias reuse, schema item title/name
  preference, operation-path fallback, and ignored generic wrapper names.
- [ ] Extend `_response_schema_fields` evidence with a bounded local
  `resource_name` derived from the enclosing object/array item schema title.
- [ ] Implement local name resolution in the tracker using Resource Catalog
  aliases, schema evidence, meaningful collection property names, and operation
  path fallback.
- [ ] Remove LLM responsibility for canonical names and aliases.
- [ ] Run the focused naming and transport tests.

### Task 3: Add exact-ID and missing-ID deterministic behavior

**Files:**
- Modify: `tests/test_resource_monitor_agent.py`
- Modify: `restscope/agent/api_behavior_monitor/resource_identifier.py`

- [ ] Add failing tests proving exact `id` wins over `userId`/`projectId`, a
  schema-only exact `id` does not persist a rule, and its absence returns
  `expected_resource_id_missing`.
- [ ] Select one observed exact normalized `id` without an LLM call.
- [ ] If exact `id` is schema-declared but unobserved, record the bounded
  missing warning and return without classification or persistence.
- [ ] Require every LLM-selected field to have at least one observed usable
  value before persisting its rule.
- [ ] Run the exact-ID tests.

### Task 4: Replace batch classification with bounded candidate selection

**Files:**
- Modify: `restscope/agent/api_behavior_monitor/resource_schemas.py`
- Modify: `restscope/agent/api_behavior_monitor/resource_identifier.py`
- Modify: `tests/test_resource_monitor_agent.py`

- [ ] Replace internal group classification test fixtures with a strict
  candidate-selection object containing only
  `identifier_candidate_id: str | None`.
- [ ] Add failing tests for `userId` requiring LLM, ID-suffix candidate
  preference, fallback to other fields, stable `50 + 50` batches, early stop,
  and ignoring candidates after the first 100.
- [ ] Add failing tests proving the second Provider call is either batch two or
  repair, never both, and invalid second output fails without partial rules.
- [ ] Build the candidate queue from valid immediate fields. Use ID-suffix
  fields when present; otherwise use all valid candidates; cap at 100.
- [ ] Send the first 50 candidates with method/path, locally resolved resource
  name, and candidate ID/path/type/format/description/observed metadata.
- [ ] Validate that the returned candidate ID belongs to the current batch.
  On valid `null`, try batch two if available. On invalid output, spend the
  remaining call on a repair of the same batch.
- [ ] Remove model-facing group IDs, canonical names, resource aliases,
  represents-resource flags, locked candidates, and negative classifications.
- [ ] Run all Resource Identifier prompt and validation tests.

### Task 5: Extract collection identifiers with partial-success warnings

**Files:**
- Modify: `restscope/agent/api_behavior_monitor/resource_identifier.py`
- Modify: `tests/test_resource_monitor_agent.py`

- [ ] Add failing tests for exactly 1000 and 1001 collection items, one
  oversized item among valid items, partial missing identifiers, and all items
  missing after a rule is learned.
- [ ] Replace the 100-value selector-wide failure with extraction over at most
  1000 resource items.
- [ ] Return typed deduplicated string/integer values plus bounded missing item
  locations.
- [ ] Persist valid identifiers even when some items are missing and record
  `expected_resource_id_missing`; when every item is missing, write no values.
- [ ] Aggregate collection truncation and per-item evidence-limit conditions
  into bounded warnings without discarding valid identifiers.
- [ ] Run focused cardinality and learned-rule tests.

### Task 6: Stop persisting negative rules and tolerate legacy negatives

**Files:**
- Modify: `restscope/db/repositories/resource_catalog_repo.py`
- Modify: `tests/test_resource_monitor_agent.py`

- [ ] Add a failing test that two valid no-selection responses each retry the
  bounded classification and leave `list_rules` empty.
- [ ] Add a failing repository-backed test that a later positive observation
  replaces an existing legacy `has_resource=false` rule.
- [ ] Stop constructing or recording negative `DetectedResourceGroup` values.
- [ ] Ignore legacy negative rules during tracker reuse.
- [ ] Permit `_upsert_rule` to replace a legacy negative row with positive
  resource evidence while keeping positive-rule conflicts fail-closed.
- [ ] Run Resource Catalog and Resource Identifier focused tests.

### Task 7: Regression verification and task completion

**Files:**
- Modify: `docs/tasks/resource-identifier-bounded-classification.md`

- [ ] Run `uv run pytest -q tests/test_resource_monitor_agent.py`.
- [ ] Run `uv run pytest -q tests/test_resource_monitor_transport.py`.
- [ ] Run
  `uv run pytest -q tests/test_app_tool_context.py tests/test_operation_smoke_agent.py`.
- [ ] Run `uv run pytest -q`.
- [ ] Run `uv run python -m compileall -q restscope`.
- [ ] Run `git diff --check`.
- [ ] Update the task record with exact observed results and remaining
  unverified live behavior.
- [ ] Leave all feature changes uncommitted until separate commit
  authorization is received.
