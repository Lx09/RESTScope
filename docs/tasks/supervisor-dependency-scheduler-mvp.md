# Supervisor Dynamic Dependency Scheduler MVP

Status: Completed

## Objective

Make Supervisor the only run entrypoint, discover operations from the supplied
OpenAPI schema, and schedule complete single-operation Schemathesis attempts
through round-based FIFO queues using runtime dependency analysis.

## Approved scope

- Create baseline checkpoints `944c70f` and `3d1379b`, then work only on branch
  `codex/supervisor-dependency-scheduler` in its dedicated worktree.
- Replace explicit operation selection and test tuning with automatic discovery.
- Replace the five OperationTestAgent stages with one Schemathesis run per
  attempt and one strict Thinking-model dependency analysis.
- Retain every attempt and expose satisfied, blocked, and fail-fast-unattempted
  operations in the final report.
- Aggregate response status codes from all Schemathesis scenario interactions.
- Keep the implementation uncommitted.

## Non-goals

- Persisting operations, dependencies, attempts, or queue state.
- Reintroducing resource grouping, value flow, operation cards, or flow graphs.
- Designing test profiles beyond Schemathesis defaults.
- Exercising a real external API or requiring Docker end-to-end testing.
- Changing Schemathesis MCP tool names or `start_run` input fields.

## Decisions

- Supervisor uses two serialized FIFO lists: `ready_queue` for the current round
  and `blocked_queue` for operations awaiting direct prerequisites.
- Initial order is stable by non-empty path-segment count and original schema
  order. A templated segment such as `{id}` counts as one segment.
- Blocked operations are reconsidered only after the current ready queue is
  empty. Only operations whose declared dependencies are all satisfied advance.
- No-progress dependency states fail immediately and report unknown
  dependencies and detected cycles; there is no retry limit.
- A dependency-issue result overrides observed 2xx until prerequisites are
  satisfied and a new complete attempt passes with an observed 2xx.
- Ordinary test failures produce global `failed`; schema, MCP, Agent, or LLM
  errors produce global `errored`. Only technical errors populate `error`.
- Headers are runtime-only: they are excluded from LangGraph state, LLM prompts,
  attempts, and final reports.
- Dependency output is not repaired. Unknown, duplicate, self-referential,
  malformed, or inconsistent output is a technical error.

## Verification

Observed on 2026-07-20 without testing a real external API:

- `uv run pytest -q tests/test_operation_agent_mvp.py tests/test_operation_agent_policy.py tests/test_main_graph_mvp.py tests/test_agent_package_boundaries.py tests/test_llm_mvp.py`
  — `39 passed`.
- MCP focused projector/run/tool/security tests — `31 passed`.
- `uv run pytest -q tests/test_schemathesis_mcp_contract.py` — `1 passed`
  against the real stdio server.
- `uv run pytest -q` at the repository root — `79 passed`.
- `uv run pytest -q -k 'not docker_stdio_mcp_host_runs_api_test'` in
  `services/schemathesis-mcp` — `50 passed, 1 skipped, 1 deselected`; the
  booking example was unavailable and the Docker test was intentionally
  excluded.
- `uv run ruff check src tests` in `services/schemathesis-mcp` — passed.
- Root Ruff was not run because Ruff is not installed in the root environment.
- `uv run python -m compileall -q restscope` and service `src` compile checks
  exited successfully.
- `git diff --check` exited successfully.
- The main worktree is clean; this feature worktree contains only the expected
  uncommitted implementation, tests, README, and task-record changes.

The implementation was initially left uncommitted as required. The user later
authorized committing both feature worktrees and merging them into `main`.
