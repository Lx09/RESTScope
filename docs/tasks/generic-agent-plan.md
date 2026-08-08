# Generic Agent Plan

Status: Completed

## Objective

Add a Profile-authorized, session-private Agent Plan without weakening or
replacing the Failure Resolution Worklist. The Plan gives a generic Main Agent
or Subagent a small read/replace Interface for task steps and progress.

## Approved scope

- Add global `plan.read` and `plan.update` Tool contracts.
- Give every Agent that explicitly selects both Tools its own in-memory Plan.
- Keep the Plan isolated from parents, children, siblings, persistence, and
  Live Observer projections.
- Preserve the current reusable-Main `Agent.run` contract. A Plan lasts for the
  Agent session even though the current product will submit only one Main task.
- Leave Failure Resolution Worklist schemas, references, finalization, and the
  four transitional named Agents unchanged.

## Non-goals

- Do not create a production Main Agent Profile in this task.
- Do not add shared planning, scheduling, recovery, database records, or UI.
- Do not call a real model, MCP server, or target API during verification.
- Do not commit, merge, push, or clean up the feature branch or worktree
  without separate authorization.

## Decisions

- `plan.read` and `plan.update` are an inseparable Profile grant.
- `plan.update` replaces the complete current Plan. It stores the latest
  optional explanation and zero to 100 bounded steps.
- Step status is `pending`, `in_progress`, or `completed`, with at most one
  `in_progress` step and no transition restrictions.
- One Harness-created Store belongs to one Agent session. The single-writer
  Interface needs neither revision numbers nor locking.
- The generic Plan and domain-specific Failure Worklist are separate Modules.

## Verification

Fresh offline verification in the dedicated feature worktree:

```bash
uv run pytest -q tests/test_agent_plan.py tests/test_tools_catalog.py \
  tests/test_agent_profile.py tests/test_agent_runtime.py \
  tests/test_subagent_runtime.py tests/test_workflow_package_boundaries.py \
  tests/test_failure_resolution_worklist.py
uv run pytest -q
uv lock --check
uv run python -m compileall -q restscope tests
git diff --check
```

- Focused Plan, Catalog, Profile, Agent, Subagent, package-boundary, and
  Failure Worklist suite: 63 passed.
- Complete repository suite: 717 passed, 18 skipped.
- Lock validation, Python bytecode compilation, and diff hygiene passed.
- The repository does not include Ruff in its dependency groups, so
  `uv run ruff check restscope tests` could not start and made no changes.
- No real model, MCP server, target API, Live Observer, or browser was used.
