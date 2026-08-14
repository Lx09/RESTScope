# Harness Test Progress and Resource Semantic States

Status: Follow-up implemented and verified; Git delivery pending authorization

## Objective

Give every fresh Orchestrator root a bounded, read-only view of attempted
positive and negative Batches, their separately executed case counts, and
current resource-instance state counts. Persist the causal resource state
changes needed to make that view auditable without moving semantic ownership
into Harness.

## Approved ownership

- API Behavior Monitor owns resource discovery, operation result-state meaning,
  instance updates, state events, and their Catalog Interfaces.
- Harness owns deterministic System Agent lifecycle and the bounded
  `test-progress` Context Reader. It does not query tables or infer states.
- The database Adapter implements only Catalog-defined reads and writes.
- Orchestrator gains one read-only Context Source and no Tool, Skill, or child
  Profile grant.

## Approved persistence

- Add immutable `result_state` to each operation/resource edge.
- Add current `semantic_state` beside each instance's complete merged JSON.
- Add append-only resource state events linked to the causal Observation.
- Commit a missing edge mapping, instance snapshots, semantic states, and final
  per-instance transitions atomically. A separately retained Observation stays
  durable when this advisory Monitor step fails.
- Apply state only to complete, untruncated 2xx JSON responses with identifiable
  resource instances.

## Approved progress view

- Count only schema-v1 `run_batch` summaries. `happy_path` is positive and
  `exceptional` is negative. Count each running, failed, or completed Batch once
  in its mode, including a valid zero-case attempt, and separately sum its
  `executed_case_count`.
- Return every OpenAPI operation, including zero Batch and case counts, and
  current resource-state counts from one Catalog read transaction.
- Render at most 12,000 characters of safe Markdown, prioritizing incomplete
  operations and nonzero resource states, with explicit whole-record omissions.
- Fail the Orchestrator root when progress cannot be read.

## Non-goals

- No App-lifetime authoritative state cache, response-body input to the state
  Agent, materialized test plan, scheduler state, or state-event detail in
  Orchestrator Context.
- No ORM, SQL, aggregation, state interpretation, or duplicate escaping logic in
  Harness.
- No Git commit, merge, push, branch deletion, or worktree cleanup without
  separate authorization.

## Implementation

- `api_behavior_monitor.resource_state` owns the stable snake-case state name,
  bounded prompt, structured FAST result, existing-name reuse, and local
  duplicate rejection. The prompt Interface accepts no response body.
- `ResourceResponseTracker` asks for a state only when the Catalog reports a
  missing operation/resource mapping. Existing edge state is reused directly;
  there is no authoritative App cache.
- One Catalog call persists the edge, recursive instance merge, current
  semantic state, and at most one final transition per Observation/instance.
  Event reads join Observation to derive operation, Batch, and Case causality.
- `APIBehaviorCatalog.read_test_progress()` returns positive/negative Batch
  attempts, their separate executed-case counts, and current resource-state
  counts from one read transaction. The SQLAlchemy Adapter owns its queries and
  single schema-v1 Batch filtering path.
- `harness.test_progress` owns the 12,000-character safe Markdown projection,
  incomplete-operation priority, whole-record omission, and explicit omission
  counts. The Orchestrator Profile names only this Context Source.
- The fresh baseline now has 12 business tables. No compatibility migration is
  added because the App continues to reject existing database files.

## Initial implementation verification

Fresh offline verification in the dedicated feature worktree:

```bash
uv run pytest -q tests/test_resource_state_contract.py \
  tests/test_api_behavior_resource_monitor.py \
  tests/test_api_behavior_catalog.py tests/test_api_response_monitor_flow.py \
  tests/test_test_progress_context.py tests/test_schema_catalog.py \
  tests/test_app_database_bootstrap.py tests/test_app_tool_context.py \
  tests/test_oracle_profiles.py tests/test_workflow_package_boundaries.py
uv run ruff check restscope tests
uv run python -m compileall -q restscope tests
uv run pytest -q tests/test_no_typing_any.py
git diff --check
uv run pytest -q
```

- Focused persistence, Monitor, progress, Profile, App bootstrap, and ownership
  suite: 83 passed.
- Ruff, Python compilation, the repository-wide AST `typing.Any` guard, and
  diff whitespace validation passed.
- Complete suite: 629 passed, 13 skipped.
- No real model, target API, MCP server, or other external service was called.
- The initial implementation was later committed as `3a81a32`, fast-forwarded
  into local `main`, verified there with 648 passed and 2 skipped, and its
  feature worktree and branch were removed.

## Batch-count follow-up

- User-approved scope: add positive and negative Batch-attempt counts beside
  the existing positive and negative executed-case counts.
- No new persistence is required because each `run_batch` call already owns one
  durable schema-v1 Batch summary.
- Focused Catalog, Context Reader, App wiring, and Profile tests: 25 passed.
- Ruff, Python compilation, the repository-wide AST `typing.Any` guard, and
  diff whitespace validation passed.
- Complete suite: 629 passed, 13 skipped.
- No real model, target API, MCP server, or other external service was called.
- Current changes are in a dedicated feature worktree and remain unstaged and
  uncommitted. Commit, merge, and cleanup require separate user authorization.
