# Task Plan: Agent Tool Runtime Simplification

## Goal

Replace the App-wide executable tool registry and role policy with one small
Agent-owned tool Module that validates and executes explicitly registered
tools. Dedup and Solve will share deterministic concurrent execution while
retaining their own reasoning loops and workflow rules.

## Current Phase

Phase 4: documentation and verification complete

## Approved Test Seams

- The public Tool Module registration, specification, single-call, and
  multi-call Interfaces.
- `FailureDedupAgent.deduplicate` for multiple independent calls.
- The public Failure Solve investigation flow for grouped queries and ordered
  session-state application.
- The optional MCP construction Interface.
- Default App construction without an App-wide executable registry or Resource
  Lookup model tool.

## Phases

### Phase 0: Isolated workspace and task record

- [x] Create `codex/agent-tool-runtime` in a dedicated worktree.
- [x] Record approved scope, non-goals, decisions, and verification plan.
- **Status:** completed

### Phase 1: Core Tool Module

- [x] RED: duplicate names, missing implementations, invalid inputs, invalid
  outputs, safe unknown errors, and deterministic concurrent results.
- [x] GREEN: one deep Tool Module hiding registration, validation, execution,
  error conversion, redaction, tracing, and concurrency.
- **Status:** completed

### Phase 2: Scoped workflow tools

- [x] Move Dedup OpenAPI and Catalog tools into its own scoped Tool Module.
- [x] Move Solve Memory, Catalog, Patch, and HTTP Probe tools into its own
  scoped Tool Module.
- [x] Keep workflow sequencing and result projection in the owning workflow.
- **Status:** completed

### Phase 3: Capability and MCP cleanup

- [x] Remove ToolPolicy, ToolSelector, the App-wide executable registry, broad
  ToolContext injection, unused ToolSpec fields, and compatibility exports.
- [x] Preserve MCP Host as an isolated explicit integration.
- [x] Remove the Resource Lookup model wrapper while preserving Coordinator
  lookup behavior and data.
- **Status:** completed

### Phase 4: Documentation and verification

- [x] Update task records, README, reading guide, and current design text.
- [x] Run focused tests, package-boundary tests, full core and tracing suites,
  compile checks, residual-name searches, and `git diff --check`.
- [x] Report all behavior not verified against live external systems.
- **Status:** completed

## Approved Decisions

- Agent tools are explicitly registered per Agent; there is no executable
  all-tools Registry or central role allowlist.
- Shared implementations are scoped before being exposed to an Agent.
- RESTScope-owned tools require input and output schemas; MCP output schemas
  remain optional when the source omits them.
- Registration requires a name, specification, and executable implementation;
  duplicate names fail and replacement is unsupported.
- Unknown exception details stay internal; model-visible failures use a stable
  safe error.
- Tool implementations bind only their explicit dependencies, not a universal
  ToolContext.
- Dedup and Solve share concurrent batch execution with no added call-count
  limit. The whole batch validates before execution, results retain call order,
  and concurrent handlers do not mutate shared Agent state.
- Agent reasoning loops and workflow sequencing remain private.
- Remove unused risk/read-only/approval/ToolSpec-timeout declarations and do
  not add replacement attributes.
- Remove the Resource Lookup model wrapper but keep underlying monitor lookup.
- Preserve MCP as an isolated opt-in integration.
- Do not preserve compatibility aliases for removed tool interfaces.

## Non-goals

- No generic Agent loop, capability permission object, tool-effect taxonomy,
  persistence, live target request, live LLM call, or external MCP process.
- No staging, commit, merge, push, worktree removal, or branch deletion without
  separate user authorization.

## Errors Encountered

| Error | Attempt | Resolution |
|---|---:|---|
| Git could not create the feature ref inside the sandbox | 1 | Re-ran the required worktree creation with approved Git metadata access. |
