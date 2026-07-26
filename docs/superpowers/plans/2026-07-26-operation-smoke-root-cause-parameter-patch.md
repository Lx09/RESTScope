# Operation Smoke Root-Cause and Parameter Patch Implementation Plan

Status: Implemented and offline-verified

## Scope

Implement the user-approved three-stage Operation Smoke runtime without
persisting diagnostic or Patch Agent state.

## Work items

- [x] Create an isolated feature worktree and `codex/` branch.
- [x] Move semantic input mapping and pure Generator preview support into
  `restscope.testing`.
- [x] Replace global PlanState diagnosis with a FIFO, per-failure investigation
  state machine.
- [x] Add atomic HTTP tool preflight, unlimited calls per valid output,
  per-failure output budgets, repair limits, probe-failure deduplication, and
  root provenance.
- [x] Add immutable FAST Patch Group routing.
- [x] Add the independent `restscope.agent.parameter_patch` package, public
  schemas, factory, skill-style prompt, local compilation, constraint solving,
  deterministic ten-sample review, and attempt limit.
- [x] Preserve system reference selections so response-value sources can be
  registered before a real candidate batch.
- [x] Run Groups serially with fresh Agent instances and provisional
  compatibility.
- [x] Stage at most one combined Generator candidate and execute one real
  same-seed candidate batch with all successful Group Constraints.
- [x] Validate only initial failure effects and apply Group-atomic acceptance,
  global-threshold acceptance, partial Generator finalization, and run-local
  Constraints.
- [x] Add distinct model roles, per-failure tracing, Patch sample tracing, Group
  run summaries, public re-exports, and Agent package-boundary coverage.
- [x] Replace obsolete tests that asserted direct Patch generation by the
  diagnoser.
- [x] Add and update task/design records.
- [x] Run the complete user-specified final verification matrix and record
  fresh results.

## Verification matrix

- `uv run pytest -q tests/test_operation_smoke_plan_solve.py`
- `uv run pytest -q tests/test_parameter_patch_agent.py`
- `uv run pytest -q tests/test_operation_smoke_agent.py tests/test_supervisor_operation_smoke.py`
- `uv run pytest -q tests/test_phoenix_tracing_contract.py tests/test_agent_package_boundaries.py`
- `uv run pytest -q`
- `uv run python -m compileall -q restscope tests`
- `git diff --check`

Fresh results: `18 passed`; `12 passed`; `15 passed`;
`6 passed, 1 skipped`; complete suite `431 passed, 16 skipped`; compileall and
diff check passed.

Offline verification does not establish a live Provider, target API, or Phoenix
service result.
