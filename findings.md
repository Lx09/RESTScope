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

## 2026-07-30 Tool-recognition diagnosis

- The user reports that Phoenix Evals identified inefficient Agent tool
  recognition or use.
- The exact Agent and inefficiency mechanism are not yet established.
- The existing GitLab live test is pre-existing user work and is outside the
  current edit scope unless later evidence makes it the correct regression
  seam.
- Current code exposes three tools to each non-checkpoint Failure Solve model
  output: Parameter memory, Parameter Patch, and a current-operation HTTP
  probe. The provider receives `tool_choice="auto"`.
- Planner has a separate read-only Failure-memory tool. Parameter Patch Agent
  itself has no tools.
- Historical Phoenix exports exist locally, including complete Project API
  smoke traces. They may provide a captured-trace replay without contacting a
  live target or model.
- The checked-in exports predate the current memory-driven Failure Solve
  implementation; they show historical HTTP-tool inefficiency but cannot
  directly prove the new Agent's tool-recognition failure.
- The configured local Phoenix endpoint is `http://127.0.0.1:6006`, and the
  live harness already contains read-only project/span export helpers.
- Docker state could not be inspected inside the current filesystem sandbox,
  so direct read-only Phoenix HTTP access is the next lower-privilege check.
- The reported Evals were recovered from the separate persisted Docker volume
  `arize-phoenix-tracing_phoenix_data`. The current Compose volume was empty.
- Phoenix contains one Plan, two Solve, and one Patch experiment run against
  repository revision `8f9a058`. Plan and Patch passed their applicable
  evaluators.
- Solve experiment 2 used the same dataset version and prompt hash as
  experiment 3, but it called the HTTP probe twice with the same request,
  never queried Parameter memory, never called Parameter Patch, ended
  `no_patch`, and scored 0 on status, memory inputs, Patch calls, and applied
  Patch count. It consumed 12 Solve outputs in about 52 seconds.
- Solve experiment 3 eventually passed every applicable evaluator, but
  consumed 20 total outputs in about 62 seconds for a scenario whose direct
  valid path is Parameter-memory lookup, one Patch call with two nested Patch
  outputs, and one final apply decision.
- This confirms the user's symptom at the current Failure Solve seam: the
  Agent sometimes fails to recognize the required tools and, even when it
  succeeds, reaches them inefficiently.
- The failing run's first two outputs each requested both HTTP and Parameter
  memory together. Runtime rejects the entire output because it allows exactly
  one tool call, but the system prompt never states that one-call rule.
- Both Solve runs tried `path/projectId` and/or `projectId`. The actual semantic
  handle is `path.projectId`. Neither tool JSON Schema enumerates allowed
  handles, and the prompt exposes the internal node path `path/projectId`
  without a discoverable semantic-handle directory. The model therefore
  guessed invalid names and consumed repeated correction rounds.
- In the passing run, the model also emitted one provider-shaped HTTP tool name
  as ordinary content, producing another correction before a canonical tool
  call. The dominant repository-controlled defect remains the ambiguous tool
  contract: valid handles and the one-call protocol are hidden from the model.

### Ranked hypotheses

1. **Hidden tool vocabulary and protocol:** If the Parameter tool schemas
   enumerate the current operation's semantic handles and the system prompt
   states exactly one tool call per output, the local replay will finish in
   three Solve calls and five total outputs.
2. **Incomplete Eval context:** If the initial run's sparse operation/config is
   the primary cause, contract guidance alone will not make the replay pass.
   The second run's 20-output path already shows this cannot be the only cause.
3. **Provider tool encoding:** If DeepSeek's tool-name encoding is primary,
   canonical schemas will still produce provider-shaped tool calls as content.
4. **Over-attractive HTTP probe:** If tool descriptions are primary, clarifying
   when probing is unnecessary will reduce probe calls after handle discovery.
5. **Continuation checkpoint:** If the checkpoint is primary, changing its
   interval will remove the waste. The trace chronology instead shows the
   errors begin before the first checkpoint.

### Local feedback loop

- Command:
  `uv run pytest -q
  tests/test_failure_solver_agent.py::test_solve_tool_contract_exposes_the_shortest_valid_tool_path`
- Fresh red result: `solve_budget_exhausted` instead of `applied_patch`;
  `1 failed in 0.18s`.
- The test drives the approved Failure Solve Interface with a deterministic
  model double that can follow only model-visible instructions and enum values.
- Hypothesis 1 was confirmed. Enumerating the current operation's dotted
  semantic handles in both Parameter tool schemas and stating the one-tool-call
  protocol in the system prompt changed the same replay to `1 passed in 0.11s`.
- The localized fix also tells Solve not to spend an HTTP probe when Batch and
  memory evidence already distinguish the root cause. No public DTO,
  persistence contract, dependency, or module boundary changed.
