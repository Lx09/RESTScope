# Orchestrator Observer Workspace

Status: Implemented and verified; Git delivery not authorized

## Objective

Adapt the local read-only Live Observer to RESTScope's current Orchestrator,
Task Executor, Subagent, and System Agent architecture without changing testing
decisions or the already-approved conversation presentation.

## Approved scope

- Replace schema-v3 Main-Agent/Todo projection with a revisioned schema-v4
  Orchestration projection over the App-lifetime Goal, Ledger, and exact Agent
  session links.
- Show a text-only Milestone, Task, and Attempt hierarchy beside separately
  identified Orchestrator sessions.
- Open Task Executor, Subagent, and other non-Orchestrator sessions in one
  right-side Drawer keyed only by full session ID.
- Preserve current left alignment, expanded muted Reasoning, compact Tool rows,
  Subagent rows, Markdown, spacing, and click-to-expand behavior.
- Persist only the latest five complete schema-v4 snapshots in same-origin
  IndexedDB and delete schema-v3 records during the approved upgrade.
- Update current architecture documentation and deterministic built assets.

## Non-goals

- No backend observer persistence, recovery input, write route, remote control,
  testing decision, additional event platform, or Agent-state registry.
- No Ant Design or other dependency upgrade.
- No restyling of the existing conversation items or detail renderers.

## Decisions

- Every conversation is keyed by `session_id`; profile names are display labels.
- The Orchestration runtime publishes only complete domain snapshots and exact
  session links through one optional fail-open read-only sink.
- Todo is removed. Task Executor private Plans remain ordinary Tool events.
- Public behavior is tested through Orchestration runtime results, Observer
  snapshot/SSE, IndexedDB records, and rendered user interactions.

## Verification

- Focused backend observation/runtime tests: passed.
- Frontend state, history, projector, component, and App tests: 46 passed.
- TypeScript/Vite production build: passed.
- ESLint and Ant Design 6.5.3 lint: passed.
- Local browser interaction: passed at exact 1440×900, 1024×800, and 375×812
  viewports with no horizontal overflow or console warning.
- Two consecutive built-asset manifests had identical SHA-256 hashes.
- Observer/Orchestration focused tests: 33 passed.
- Complete Python suite: 659 passed, 13 skipped.
- Ruff, Python compilation, `typing.Any`, package-cycle, and diff checks: passed.
