# Global Tools and Agent Harness Foundation

Status: Completed

## Objective

Replace the workflow-owned Agent and Tool rules with the approved Main Agent,
Subagent, Skill, Tool, and Harness language. Establish an Agent Profile, one
global built-in Tool Catalog grouped by the thing each Tool handles, and a
deterministic Harness that binds only the Tools, Skills, and bounded context
sources selected for one Agent.

## Approved scope

- Move every RESTScope-owned model Tool into `restscope.tools` without changing
  its model-visible name or domain behavior.
- Make each Tool Module own its complete ToolSpec, execution Adapter, safe
  failure translation, and directly supporting presentation code.
- Move Skill metadata into `restscope.skills` and make Agent Profile selection
  explicit instead of role-driven.
- Replace CapabilityRuntime with HarnessRuntime and keep built-in and external
  MCP Tool Catalogs separate.
- Split deterministic request generation and execution into
  `restscope.harness.testing`; remove the old `restscope.testing` and
  `restscope.capabilities` import paths without compatibility aliases.
- Preserve the current named LLM Agents as a documented migration exception;
  their later conversion to one Agent class plus Skills/Subagents is separate.
- Replace conflicting active rules and record the architecture decision.

## Non-goals

- Do not build the final Main Agent or convert the existing Resolution, Patch,
  Compact, or Review Agents in this task.
- Do not change persistence, Worklist finalization, target HTTP authorization,
  Operation Smoke scheduling, or model-visible Tool names.
- Do not expose raw logs to a model or call a real model, MCP server, or target
  API during verification.
- Do not commit, merge, push, or remove the feature worktree without separate
  Git authorization.

## Decisions and invariants

- Global discovery does not grant execution permission. An Agent Profile names
  the exact Tools, Skills, and context sources it may receive.
- Built-in Tool definitions are immutable and authoritative. Runtime MCP Tools
  live in a separate external Catalog and are never injected automatically.
- Provider strict mode is optional. RESTScope always validates input locally,
  validates successful structured output, and converts expected cross-field
  mistakes into safe Tool failures before state changes.
- The Worklist apply/no-Patch candidate rule remains the regression baseline:
  invalid calls preserve revision and may be corrected in the same session.
- Raw logs remain human observability data. Agent-visible evidence is bounded,
  structured, and redacted.

## Verification

Fresh verification completed in the feature worktree:

```bash
uv run pytest -q tests/test_tools_catalog.py tests/test_agent_profile.py tests/test_workflow_package_boundaries.py tests/test_failure_resolution_worklist.py tests/test_failure_resolution_agent.py
uv run pytest -q
git diff --check
```

- Focused Catalog, Profile, boundary, Worklist, and Agent tests: 59 passed.
- Full repository suite: 693 passed, 18 skipped.
- Python bytecode compilation completed without errors.
- `git diff --check` completed without errors.
- No real model, MCP server, or target API was called.

## Approved Profile-authorized Agent runtime follow-up

Status: Implemented and locally verified; intentionally uncommitted

The user approved a generic, Profile-authorized Agent runtime without adding
any concrete business Profile or migrating the four transitional named Agents.
The Harness will become the only construction seam for one reusable Agent
class, resolve model configuration, Tools, Skills, bounded context sources, and
allowed child Profiles before launch, and expose asynchronous `subagent.start`,
`subagent.wait`, and `subagent.cancel` Tools from the global Catalog.

Main and child Agents share an in-memory, Codex-style tree control for weighted
rollout tokens, open Agent slots, and active execution slots while retaining
independent Context and turns. Profile call graphs are explicit and acyclic,
child depth is limited to three, Main history is bounded and compacted at 80%,
and no Agent state, queue, checkpoint, raw log, or transcript is persisted.

The implementation remains in this feature worktree. It does not authorize a
commit, merge, push, live model call, MCP process, target request, branch
deletion, or worktree cleanup.

### Implemented runtime

- `AgentProfile` now names a model configuration and authorized child Profiles;
  no `model_role` compatibility field remains.
- Immutable Profile and loaded-Skill registries validate the complete model,
  Provider, Tool, Binding, Skill, Context Source, and child graph before launch.
- `HarnessRuntime.start_main_agent` is the only public construction seam. The
  former `ResolvedAgentAccess` and `validate_profile` shallow interface was
  deleted. A default Harness reports `agent_runtime_not_configured`.
- One generic `Agent` owns the one-Tool-or-final correction loop, bounded Main
  history, fixed `AgentCompletion` contract, cooperative cancellation, exact
  Profile Tool payload, and 80% same-model Tool-free compaction.
- The global Catalog now includes the deep, closed `subagent.start`,
  `subagent.wait`, and `subagent.cancel` contracts. The Harness binds them to a
  direct-parent tree view; Profile DAG depth, authorization, open slots,
  collection, timeout, and cancellation remain deterministic.
- One App-memory tree control owns four-open/four-active defaults and a 1,000,000
  weighted-token rollout budget. Provider-reported cached input is excluded
  from the input charge, 50%/25%/10% waterline reminders are one-shot, and an
  over-budget response cannot execute its Tool action.
- Main and child Agent/LLM/Tool spans retain tracing parentage. No Profile,
  objective, history, result queue, budget, cancellation state, or compacted
  summary is persisted.

### Follow-up verification

Fresh verification completed in the feature worktree:

```bash
uv run pytest -q tests/test_agent_runtime.py tests/test_agent_profile.py tests/test_subagent_runtime.py tests/test_tools_catalog.py tests/test_agent_tools.py tests/test_agent_context.py tests/test_llm_mvp.py tests/test_mcp_adapter.py tests/test_workflow_package_boundaries.py tests/test_failure_resolution_worklist.py tests/test_failure_resolution_agent.py
uv run pytest -q
uv lock --check
uv run python -m compileall -q restscope
git diff --check
```

- Focused Agent, Profile, Harness, Subagent, Catalog, provider, Context, and
  Worklist suite: 148 passed.
- Complete repository suite: 712 passed, 18 skipped.
- Lock validation, bytecode compilation, and diff hygiene completed without
  errors.
- No real model, MCP server, target API, commit, merge, push, or cleanup ran.

## Approved LangGraph removal follow-up

The user approved replacing the four-node synchronous LangGraph wrapper with a
plain run-scoped Harness loop. This follow-up keeps operation discovery, stable
FIFO ordering, cross-round retries, global provider-failure stopping, reports,
and tracing behavior while removing `restscope.supervisor`,
`RESTScopeMainGraph`, and the LangGraph dependency.

The public `RESTScopeApp.run`, `RESTScopeRunRequest`, and
`RESTScopeRunReport` Interfaces remain. Direct `restscope.supervisor` imports,
old Graph trace names, and Graph-specific report metadata are intentionally not
preserved. Scheduler queues remain ephemeral and no checkpoint, persistence,
parallel execution, Main Agent scheduling, or Subagent scheduling is added.

Verification for this follow-up will cover the new `RunHarness.run` Interface,
App integration, tracing, observer behavior, package boundaries, dependency
removal, compilation, the complete test suite, and diff hygiene.

### Follow-up verification

Fresh verification completed in the same feature worktree:

```bash
uv run pytest -q tests/test_harness_run.py tests/test_smoke_tracking.py tests/test_phoenix_tracing_contract.py tests/test_observability_integration.py tests/test_app_tool_context.py tests/test_live_ui_app.py tests/test_workflow_package_boundaries.py
uv run pytest -q
uv lock --check
uv run python -m compileall -q restscope
git diff --check
```

- Focused Run Harness, App, tracing, observer, and boundary suite: 33 passed,
  3 skipped.
- Complete repository suite: 696 passed, 18 skipped.
- LangGraph and its now-unused dependency subtree are absent from the project
  lock and resolved dependency tree.
- Current production code, active rules, README, code guide, and test guide
  contain no old Supervisor package or Main Graph names.
- No real model, MCP server, or target API was called.
