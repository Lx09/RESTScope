# Planner Agent v1

Status: Completed

Superseded for the active architecture by
`docs/tasks/schema-source-persistence-redesign.md`. This record remains as
history of the former ten-table database-backed Planner experiment.

## Objective

Implement the user-approved Planner Agent design: initialize one OpenAPI
catalog exactly once, persist the complete normalized document and derived
operation graph, then generate versioned test-requirement plans using only
database evidence.

## Approved scope

- Add an explicit, atomic OpenAPI catalog initialization API.
- Persist catalog readiness, normalized OpenAPI JSON, diagnostics, operation
  cards, initial intelligence, and operation edges.
- Add a database-only Planner Agent and public factory.
- Generate complete immutable plan revisions containing independent
  single-operation and workflow requirements.
- Allow one structured LLM repair attempt and persist only valid plans.
- Replace the Planner-specific Schemathesis campaign contract with a generic
  test-requirement planning contract.

## Non-goals

- Updating, replacing, synchronizing, or versioning an initialized schema.
- Supporting multiple ready schemas in v1.
- Executing or dispatching TestAgent work.
- Changing OperationTestAgent or RESTScopeMainGraph execution behavior.
- Giving Planner tool access or emitting Schemathesis configuration.

## Decisions and assumptions

- Initialization checks for an existing ready catalog before reading the
  supplied source; every later initialization attempt is rejected.
- Parser errors roll back all writes; parser warnings are retained.
- The source URI is provenance only after initialization.
- Each Planner invocation emits a complete replacement revision. Requirement
  IDs are new in each revision; only plans have predecessor linkage.
- The Agent is bound to one ready `schema_id`; requests contain only `task_id`.

## Verification

Planned commands:

```bash
uv run pytest -q tests/test_openapi_catalog.py
uv run pytest -q tests/test_planner_agent.py tests/test_context_mvp.py tests/test_memory_mvp.py tests/test_llm_mvp.py
uv run pytest -q
git diff --check
```

Observed results (2026-07-15):

- `uv run pytest -q tests/test_openapi_catalog.py tests/test_planner_agent.py`
  passed during focused development.
- `uv run python -m compileall -q restscope` exited successfully.
- `git diff --check` exited successfully.
- `uv run pytest -q` completed with `75 passed in 0.90s` before this record
  was finalized.

## Remaining risks

- Real provider behavior will be covered by the existing provider abstraction;
  acceptance tests use a deterministic fake and do not make live LLM calls.
- Very large OpenAPI documents remain subject to the configured context token
  budget; identifiers must remain present while descriptive detail may be
  compressed.
- External `$ref` sources are read during the one-time parser pass, but separate
  multi-file bundling behavior was not exercised by this task's acceptance
  suite. Planner itself consumes only the persisted catalog after bootstrap.
