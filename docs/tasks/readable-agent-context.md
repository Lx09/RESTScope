# Readable Agent Context

## Status

Completed. Commit `f68fe87` was fast-forwarded into local `main`, the merged
Context/Patch tests passed, and the feature worktree and branch were removed.
The local branch has not been pushed.

## Objective and approved scope

Replace the project-wide typed, dotted-line prompt notation with one readable
Markdown-card format while retaining the existing `CompactTextWriter`
Interface. The shared Context Module continues to own escaping, character
budgets, clipping, optional-history omission, safe HTTP JSON fences, and
metrics. Each workflow continues to choose and summarize its own domain facts.

The Parameter Patch projection is also being narrowed so it shows only the
current requirement, affected Generator facts, semantic Constraints, usable
reference aliases, and relevant applied or conflicting history.

## Non-goals

- Do not add a style or compatibility mode to `CompactTextWriter`.
- Do not move workflow-specific relevance decisions into the Context Module.
- Do not change `AgentContext`, `ContextMetrics`, tool-call grouping, or JSON
  tool/final-output protocols.
- Do not call DeepSeek, GitLab, Phoenix, or another live external system.
- Do not commit, merge, push, or remove this feature worktree without separate
  authorization.

## Decisions and assumptions

- Strings use JSON quoting and escaping without a type prefix.
- Missing, null, empty arrays, and empty objects remain four distinct values.
- A Writer record is an atomic Markdown card, so optional history can still be
  removed without leaving half of a record behind.
- Long collections remain complete at the Writer seam. A workflow may provide
  an explicit domain summary, such as the approved Parameter Patch choice-value
  preview, when the complete collection is not useful.
- Parameter Patch system-protocol wording remains outside this feature. The
  dirty main worktree now uses a JSON Schema response protocol, so duplicating
  its earlier strict-tool wording here would be stale and would create a merge
  conflict. This branch changes only the model-facing evidence projection.

## Verification

- Context, Dedup, Solve, Patch/Review, Behavior Monitor, tracing, evaluation,
  and package-boundary tests:
  `uv run pytest -q tests/test_agent_context.py
  tests/test_failure_dedup_agent.py tests/test_failure_solver_agent.py
  tests/test_parameter_patch_agent.py tests/test_resource_identifier_tracker.py
  tests/test_api_behavior_response_value.py tests/test_smoke_tracking.py
  tests/test_operation_smoke_evaluations.py
  tests/test_workflow_package_boundaries.py`: `118 passed, 2 skipped`.
- Complete local suite: `uv run pytest -q`: `560 passed, 18 skipped`.
- Clean merged-main Context and Parameter Patch tests: `39 passed`.
- After restoring the pre-existing main-worktree changes, the combined Context,
  Dedup, Solve, Patch/Review, and Behavior Monitor tests passed: `116 passed`.
- `uv run python -m compileall -q restscope evaluations tests`: passed.
- `git diff --check`: passed.
- A local rendering of the reported GitLab updated-at case was inspected. It
  contains separate requirement facts, three Generator cards, a multiline
  choice list, and one no-history sentence; it contains no typed prefixes,
  dotted list indexes, internal IDs, or unrelated `query.sort` evidence.
- No DeepSeek, GitLab, Phoenix, or other live external request was made.

## Remaining risks

- Model readability has been checked through deterministic prompt rendering,
  not a separately authorized live model call.
- The local commits have not been pushed; push requires separate authorization.
