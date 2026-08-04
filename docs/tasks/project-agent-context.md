# Project Agent Context and Planner Retrieval

Status: Implemented and verified

> Readability update (2026-08-04): the original typed-line presentation is
> superseded by the single Markdown-card format recorded in
> `readable-agent-context.md`. The public Context facade, safety ownership,
> budgets, tool-group preservation, and metrics remain unchanged.

## Objective

Give every direct RESTScope LLM decision one small, reusable Context Interface
for safe compact text and bounded multi-turn messages. Reduce Planner's input
from a complete Failure directory plus memory tool to deterministic,
same-operation candidate retrieval.

## Approved scope

- Public `restscope.context` facade with exactly `AgentContext`,
  `CompactTextWriter`, `ContextLimits`, and `ContextMetrics`.
- Composition only: no Agent base class, role registry, Context persistence,
  snapshot, checksum, or database dependency.
- Bounded Markdown cards for runtime-generated DTO, Memory, API, tool, and
  sample evidence. Final Agent output and provider tool protocols remain JSON.
- Injection-resistant value encoding, explicit clipping/omission markers,
  complete assistant/tool groups, newest validation feedback, and numeric trace
  metrics.
- Migrate Plan, Solve, Patch, Resource Identifier selection, and Response Value
  Source selection.
- Planner uses structured retrieval signals and receives at most three
  candidates per failed observation and 24 unique candidates overall. It has no
  memory tool.
- Keep production DTO validation, Patch compilation/sampling, workflow,
  database writes, and stop reasons unchanged.
- Update Phoenix Scenario adapters and code evaluators to reflect pre-call
  candidate retrieval.

## Non-goals

- No embeddings, vector database, database migration, target API call, or new
  persistence.
- No live DeepSeek or Phoenix Experiment without separate authorization.
- No return of the deleted `ContextPolicyRegistry`, `ContextPackage`,
  `SourceRef`, role policy system, or compatibility aliases.
- No change to Agent final JSON DTOs or provider tool JSON Schema.
- No repair of unrelated baseline failures in Generator enablement or one
  execution test's stale `.report` access.

## Implemented design

`CompactTextWriter` owns JSON-style scalar notation, recursive Markdown cards,
tables, untrusted section labels, control-character escaping, per-value
clipping, and optional-history omission. `AgentContext` owns the initial
system/task pair,
assistant/tool groups, validation feedback, provider-window projection, and
trace-safe numeric metrics.

Planner creates a `FailureRetrievalObservation` for every failed `C` case.
`SmokeMemory.find_failure_candidates` searches only the operation's existing
Failure histories. Status or media alone is insufficient; candidates require
an error signature, causal Parameter, kind/status plus input path, useful term,
or transport match. Runtime ranks and merges at most three per observation,
then Planner caps the combined request at 24 `F` cards.

Solve preloads only the Todo's cases and compact current Failure history. Its
Parameter Memory, HTTP Probe, and Patch candidate feedback are bounded text;
compatibility-critical applied Patch/conflict history is never silently
discarded. Patch receives affected Generators, active Constraints, reference
aliases, and compatibility facts, then reviews normalized presence/value
samples.

The two Behavior Monitor selectors use the same writer/context boundary, which
keeps the shared Module independent from Operation Smoke.

## Verification

Fresh final results:

- Context, package boundary, Agent, Memory, Behavior Monitor, tracing, and
  Phoenix evaluation concentration:
  `uv run --group evaluation pytest -q tests/test_agent_context.py
  tests/test_workflow_package_boundaries.py tests/test_smoke_plan_agent.py
  tests/test_operation_smoke_memory.py tests/test_failure_solver_agent.py
  tests/test_parameter_patch_agent.py tests/test_resource_identifier_tracker.py
  tests/test_api_behavior_response_value.py
  tests/test_operation_smoke_evaluations.py tests/test_smoke_tracking.py`:
  `99 passed`.
- `uv run pytest -q`: `489 passed, 4 skipped`, plus the two unchanged baseline
  failures listed below.
- `uv run --extra tracing pytest -q`: `489 passed, 4 skipped`, plus the same
  two unchanged baseline failures.
- `uv run python -m compileall -q restscope evaluations tests`: passed.
- Residual scans found no old source `prompt_context`, fitter,
  `FailureCatalogPromptEntry`, Planner memory tool/evaluator, or JSON example
  prompt protocol.

The pre-change local `main` independently reproduces the two unrelated suite
failures:

- `test_object_cardinality_requires_a_generator_set_that_always_conforms`
- `test_smoke_execution_applies_constraints_and_traces_only_the_count`

No real model, Phoenix service, or target API is used in this task.
