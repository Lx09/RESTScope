# Task Plan: Operation Smoke Memory Workflow

## Goal

Replace Effect validation with a memory-driven Batch → Plan → Solve → Patch
loop, backed by App-lifetime database records and one App-wide generation seed.
Then diagnose and resolve the Phoenix-Evals finding that an Agent recognizes or
uses its tools inefficiently, without broadening the approved runtime
architecture.

## Current Phase

Completed

## Phases

### Phase 1: Contracts, glossary, and global seed

- [x] Record the approved domain language and task scope.
- [x] Add failing tests for the public Smoke stop contract and App-wide seed.
- [x] Implement the smallest shared randomness and DTO changes.
- **Status:** completed

### Phase 2: Smoke Memory Module and database baseline

- [x] Add behavior-first tests at the Memory Interface.
- [x] Implement Failure, Observation, Investigation, Parameter, and Applied
  Patch persistence.
- [x] Replace the migration chain with one current baseline.
- **Status:** completed

### Phase 3: Planner memory workflow

- [x] Add red tests for Failure reuse, Observation coverage, and history lookup.
- [x] Implement Planner memory tools and deterministic writes.
- **Status:** completed

### Phase 4: Solve-owned Patch tool workflow

- [x] Add red tests for Patch tool calls, candidate refs, retries, and conflicts.
- [x] Implement Solve memory reads, Patch tool execution, and atomic apply.
- **Status:** completed

### Phase 5: Coordinator simplification and Effect removal

- [x] Add red tests for complete-Plan batching and three passed stop reasons.
- [x] Remove Effect runtime, public fields, role, package, and compatibility.
- **Status:** completed

### Phase 6: Documentation and final verification

- [x] Update current rules, README, reading guide, boundary tests, and task record.
- [x] Run focused, core, tracing, compile, diff, and residual checks.
- [x] Leave all work unstaged and uncommitted.
- **Status:** completed

### Phase 7: Authorized GitLab live acceptance

- [x] Build a one-operation `POST /projects` live feedback loop.
- [x] Diagnose Cookie-only 401 responses and add the required CSRF header.
- [x] Fix trusted-header leakage with a red/green regression test.
- [x] Reach 100% Batch success and audit the complete Phoenix trace.
- **Status:** completed

### Phase 8: Agent tool-recognition diagnosis

- [x] Build a fast, deterministic, red-capable local feedback loop for the
  inefficient tool-selection behavior.
- [x] Minimize the reproduction and rank falsifiable hypotheses.
- [x] Identify the narrowest fix and request approval first if it changes a
  public contract or architectural boundary.
- [x] Add the regression test, apply the approved/localized fix, and run fresh
  focused plus proportional verification.
- **Status:** completed

## Phase 8 feedback loop

`uv run pytest -q
tests/test_failure_solver_agent.py::test_solve_tool_contract_exposes_the_shortest_valid_tool_path`

- Pre-fix result: red, `solve_budget_exhausted` instead of `applied_patch`.
- Fixed result: green in three Solve model calls and five total
  outputs, including the nested two-output Patch Agent call.
- Test seam: the previously user-approved public Failure Solve
  `start(...).advance()` Interface.

## Decisions Made

| Decision | Rationale |
|---|---|
| Keep the current workflow-package refactor | The approved memory workflow builds directly on those seams. |
| Memory is database-backed but read only within one App lifetime | The design is still changing and does not yet support cross-App recovery. |
| Only applied Patches are durable | Rejected candidates remain Solve-session evidence. |
| No Effect Agent or candidate rollback lifecycle | The next complete Batch is the effect evidence. |
| One App-wide seed controls generated test values | Reproducibility has one configuration source; UUID identities remain independent. |
| Test the Planner, Solve/Patch, Coordinator, Memory, and Randomness Interfaces | These are the user-approved seams from the final plan. |

## Errors Encountered

| Error | Attempt | Resolution |
|---|---:|---|
| Repository-local planning skill path did not exist | 1 | Loaded the installed skill from `/Users/lixin/.agents/skills/`. |
| First App/Coordinator patch used an imprecise context line | 1 | Inspected the exact constructor and factory text, then applied smaller hunks. |
| Focused test command named a nonexistent App test node | 1 | Located real test node names before rerunning a different focused selection. |
| Combined Memory export patch missed one context | 1 | Split the change into precise file-level patches. |
| Eager workflow facades caused a database import cycle | 1 | Made approved facade names lazy without adding aliases. |
| GitLab Cookie authenticated reads but POST returned 401 | 1 | Added the authenticated page's CSRF token as a trusted, in-memory header. |
| Batch summaries exposed trusted authentication headers | 1 | Redacted sensitive header values at the public request-summary boundary while preserving transport headers. |
| Live harness asserted the retired Batch span name | 1 | Updated the assertion to `OperationTestingService.run_smoke_batch`. |

## Notes

- Do not invoke real LLMs, Project API, or Phoenix.
- Do not stage, commit, merge, or remove the worktree.
- Final GitLab live run:
  `gitlab-post-projects-smoke-20260729T045826Z-fccf0c6f`.
