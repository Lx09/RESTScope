# Findings and Decisions

## Requirements

- Run Batch Testing before every Planner decision.
- Planner classifies every Failure Observation or explicitly marks it
  non-debuggable.
- Planner can query historical Failures through a read-only memory tool.
- Solve can query parameter history, optionally probe HTTP, and call Patch
  Agent as a side-effect-free LLM tool.
- Solve applies only a validated candidate reference; all Plan items finish
  before the next Batch.
- Remove Effect validation and all compatibility names.
- Store structured Failure, Investigation, Parameter, and applied Patch memory
  in the current App database.
- Use one App-wide configurable seed for all generated test values.

## Research Findings

- The current coordinator accepts database candidates only after
  `SmokeEffectAgent` returns `resolved_without_regression`.
- Generator configurations and rollback revisions currently use a SQLAlchemy
  catalog and migrations `0001` through `0006`.
- Existing `OperationSmokeHistory` keeps complete raw evidence in memory but
  cannot query by stable Failure or Parameter identity.
- Current generation already uses deterministic per-node derivation from a run
  seed; the new Randomness Module can deepen that existing behavior behind one
  App-owned Interface.
- `RESTScopeConfig` is a frozen dataclass graph, so resolving an absent seed
  once with `dataclasses.replace` keeps every subsequently built collaborator
  on the same value without adding a second public injection parameter.
- `RESTScopeMainGraph` constructs the public run report, making it the narrow
  place to expose the resolved App seed.
- Planner currently has no tools and returns expanded todos without stable
  Failure memory.
- Failure Solve currently returns a Patch requirement to the Coordinator;
  Patch Agent and Effect Agent are invoked outside the Solve session.
- Eager package facades create persistence/workflow import cycles once Memory
  is workflow-owned. Lazy approved-name resolution preserves the same public
  Interface without restoring an old name or path.
- GitLab's root session Cookie authenticates read APIs, but write APIs return
  401 unless the authenticated page's CSRF token is also supplied.
- The pre-live request-summary boundary copied trusted authentication headers
  into public Batch reports and case traces. Redacting by sensitive header name
  before constructing the summary preserves transport behavior while keeping
  credentials out of Planner, reports, and Phoenix.
- A fully successful initial Batch correctly produces no Planner, Solve,
  Parameter Patch, or LLM spans. The Coordinator stops immediately at the
  success threshold rather than invoking Agents unnecessarily.

## Technical Decisions

| Decision | Rationale |
|---|---|
| Operation Smoke owns a `memory` Module | Planner and Solve share identical semantics and App lifecycle. |
| Database adapters remain under `restscope.db` | Persistence implementation stays behind the Memory Interface. |
| Planner and Solve tools expose request-local aliases | Models cannot forge database identities or cross operations. |
| Generator apply, Investigation, Parameter links, and Applied Patch commit atomically | Configuration and memory cannot disagree. |
| Migration history is replaced by one current baseline | Existing App startup already rejects old database files. |
| `SeededRandom` uses scope-derived independent streams | One extra random call cannot shift unrelated generated values. |
| Smoke memory UoW exposes memory and Generator repositories on one session | Accepted Generator state and its explanatory record can commit or roll back together without coupling the repositories. |
| Sensitive merged headers become `[redacted]` at the public request-summary boundary | The transport still receives credentials, while reports, Planner evidence, and traces cannot retain them. |

## Issues Encountered

| Issue | Resolution |
|---|---|
| Current source-and-decisions rule forbids Agent memory persistence | The user's approved plan explicitly supersedes that rule; AGENTS.md and the task record will be updated. |

## Resources

- `restscope/operation_smoke/coordinator.py`
- `restscope/operation_smoke/plan/`
- `restscope/operation_smoke/failure_solver/`
- `restscope/testing/catalog.py`
- `restscope/db/migrations/versions/`
