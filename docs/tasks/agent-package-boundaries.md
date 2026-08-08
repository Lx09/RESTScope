# Agent Package Boundaries

> Superseded architecture note (2026-08-08): ADR 0001 replaces the
> class-per-Agent package rule and Supervisor boundary with one configurable
> Agent class, explicit Profiles, global Tools, and a deterministic Harness.
> This task remains historical evidence only.

Status: Completed

## Objective

Make an independent package per Agent a hard project constraint and reorganize
the existing Planner, OperationTest, and Supervisor implementations without
changing their public behavior.

## Approved scope

- Record the package boundary in `AGENTS.md` as a required project rule.
- Move Planner into `restscope/agent/planner/`.
- Move OperationTestAgent into `restscope/agent/operation_test/`.
- Move the supervisor graph into `restscope/agent/supervisor/`.
- Keep `restscope.agent` and top-level `restscope` imports compatible.
- Add an executable architecture test that rejects implementation modules at
  the root of `restscope/agent/`.

## Non-goals

- Change Agent runtime behavior, request/response fields, or database models.
- Introduce a generic Agent base class or speculative shared contracts.
- Commit, push, merge, or remove the current worktree.

## Decisions

- An Agent package owns its runtime, schemas, state, and directly supporting
  services.
- Supervisor is treated as an orchestration Agent boundary with its own package.
- `restscope/agent/__init__.py` is a compatibility facade, not an implementation
  module.
- Shared packages are created only after two real consumers have identical
  semantics and lifecycle requirements.

## Verification

Planned commands:

```bash
uv run pytest -q tests/test_agent_package_boundaries.py
uv run pytest -q tests/test_operation_agent_mvp.py tests/test_main_graph_mvp.py tests/test_planner_agent.py
uv run python -m compileall -q restscope
git diff --check
uv run pytest -q
```

Observed results (2026-07-15):

- The package-boundary test was observed failing against the flat module
  layout, then passed after the reorganization.
- `uv run pytest -q tests/test_agent_package_boundaries.py` completed with
  `3 passed`.
- `uv run pytest -q tests/test_operation_agent_mvp.py tests/test_operation_agent_policy.py tests/test_main_graph_mvp.py tests/test_planner_agent.py`
  completed with `22 passed`.
- `uv run python -m compileall -q restscope` exited successfully.
- `git diff --check` exited successfully.
- `uv run pytest -q` completed with `78 passed in 0.95s` before this record
  was finalized.

## Remaining risks

- The stable `restscope.agent` and top-level `restscope` facades are preserved.
  Direct imports of the former private flat modules are intentionally removed
  because they conflict with the new hard package constraint.
