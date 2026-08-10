# System Agent Profile Monitor Migration

Status: Delivered to local main and verified

## Objective

Move the API Behavior Monitor's two ambiguous LLM classifications onto
Profile-authorized System Agents while preserving the Main Agent and Subagent
contracts, Monitor batching and persistence, and browser schema-v3 history.

## Approved scope and decisions

- Keep `AgentProfile` fixed and add `SystemAgentDefinition` beside runtime
  assembly to bind only task input and result validation.
- Add repeatable synchronous `run_system_agent`; each invocation is an isolated
  root tree and shutdown cancels every active root and descendant.
- Give System roots unrestricted token accounting and unlimited invalid-output
  correction. Only terminal runtime events stop the loop.
- Register no-Tool `fast` Profiles for resource identifier and response source
  selection. Continue to expose only task-local `I*` and `S*` aliases.
- Replace Tracker LLM infrastructure dependencies with a narrow runner and use
  a private bind-once App adapter to resolve the HTTP transport composition
  cycle.
- Remove `ModelSelector`, rename model configuration `role` to `name`, and keep
  all Provider, context, Schema, and validation infrastructure.
- Associate independent System roots to the active HTTP Tool only through
  `parent_event_id`. Show their status and complete conversations in an
  accessible Drawer without changing schema-v3 persistence.

## Implementation notes

- The generic Agent now has explicit `main`, `subagent`, and `system`
  lifecycles. Main completion and weighted tree budget behavior are unchanged.
- System output Schemas are generated per task and are checked again against
  the exact candidate set before the Monitor sees restored domain references.
- Deterministic Monitor matches still skip all model work. Terminal model
  failure follows the existing warning path and does not mark successful HTTP
  transport as failed.
- Existing browser snapshots remain ordinary Agent-turn and Tool-call event
  collections; the projector derives HTTP Tool nesting rather than copying or
  rewriting stored events.

## Verification record

- System lifecycle tests (`uv run pytest -q
  tests/test_system_agent_runtime.py`): 7 passed. This covers four-turn output
  correction, repeated isolated roots, task adaptation, exact Tool grants,
  unbounded accounting, Provider failure cleanup, and shutdown cancellation.
- Focused boundary, Monitor, and Observer tests (`uv run pytest -q
  tests/test_workflow_package_boundaries.py tests/test_system_agent_runtime.py
  tests/test_resource_identifier_tracker.py
  tests/test_api_behavior_response_value.py
  tests/test_observability_integration.py tests/test_live_run_observer.py`):
  88 passed, 1 skipped before the final task-adapter-only refinement; the final
  complete suite below includes that refinement.
- Complete Python suite (`uv run pytest -q`): 573 passed, 14 skipped.
- Python bytecode compilation (`uv run python -m compileall -q restscope
  tests`): passed.
- Frontend (`npm test -- --run`, `npm run lint`, `npm run build`): 8 Vitest
  files / 44 tests passed; ESLint passed; TypeScript and Vite production build
  passed. Vite reported only its existing advisory that the minified JavaScript
  chunk exceeds 1,000 kB.
- Ant Design source check (`antd lint ./src --format json`): 0 issues and no
  skipped files.
- Production residual scans found no `ModelSelector`, `model_selector`,
  Tracker-owned LLM infrastructure type, or `is_subagent` reference. Direct
  `client.invoke` calls remain only in the generic Agent runtime.
- `git diff --check`: passed.
- After the feature commit was merged into local `main`, fresh verification on
  the merged tree passed 592 Python tests with 3 skips, Python bytecode
  compilation, 8 frontend test files / 44 tests, ESLint, the TypeScript/Vite
  production build, and the Ant Design source check with 0 issues.

## Git delivery

The scoped implementation was committed as `12d2120` and merged into local
`main` as `18fde74`. The dedicated worktree and
`codex/system-agent-profile` branch were removed after merged-tree
verification. Nothing was pushed.
