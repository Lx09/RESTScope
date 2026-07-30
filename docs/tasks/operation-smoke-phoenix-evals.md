# Operation Smoke Phoenix Evals

Status: Implemented, verified, and merged into local `main`

Subsequent note: `docs/tasks/project-agent-context.md` replaces the original
model-facing prompt projections and Planner memory-tool assumption. The
Phoenix Dataset/Experiment structure remains active.

## Objective

Evaluate `SmokePlanAgent`, `FailureSolveAgent`, and `ParameterPatchAgent`
independently with Phoenix-native Datasets, Experiments, code evaluators, and
linked RESTScope traces. Make it straightforward to add a Scenario or another
Agent without creating a second evaluation framework.

## Approved scope

- Add an `evaluation` development dependency group for the official Phoenix
  client, Evals, and tracing packages.
- Keep sanitized YAML Scenarios in the repository as the source of truth and
  synchronize one Dataset per Agent with stable Scenario IDs.
- Run the real production Agent and configured DeepSeek model for every task.
- Give every Scenario/repetition fresh temporary collaborators.
- Script Solve's Memory, HTTP Probe, nested Patch result, and Patch application
  without sending a target request or writing a database.
- Run Patch's real DTO validation, semantic compilation, deterministic
  generation, sample review, and Constraint validation.
- Score each declared requirement independently with Phoenix code evaluators;
  use `not_applicable` for undeclared requirements and keep `runtime_error`
  separate.
- Support complete system-prompt variants without changing production defaults.

## Non-goals

- No LLM Judge, aggregate pass/fail, plugin registry, evaluator DSL, custom
  result store, or human-annotation contract.
- No Operation Smoke workflow, persistence, target transport, prompt, Generator,
  or Constraint behavior change.
- No target API request.
- No automatic write-back from a candidate prompt to production.
- No commit, merge, worktree removal, or branch deletion without separate user
  authorization.

## Evidence and decisions

The initial nine Scenarios are sanitized translations of evidence in the
ignored export
`artifacts/phoenix-exports/restscope-project-swagger-smoke-20260727T010238Z-9712a1cf/`.
The trace is provenance, not an expected-answer oracle. The curated cases cover
duplicate/split/reused Planner Failures, Solve memory/probe/Patch/conflict
choices, bounded and history-compatible Generators, and a multi-Parameter
Constraint.

Phoenix Experiment quality results—including a low score or one task-level
`runtime_error`—are evaluation evidence and do not make the CLI fail.
Scenario/configuration, Dataset/Phoenix, DeepSeek, or Experiment infrastructure
failure does.

## Verification

Deterministic evaluation and prompt-override tests pass. They cover Scenario
validation, stable Dataset mapping, the explicit suite registry, fresh
temporary collaborators, compact tool-call records, prompt overrides,
Experiment metadata, production Patch compilation/sampling, and 1/0/N/A code
evaluator behavior.

The ordinary full suite retains two pre-existing local-main failures:
`test_object_cardinality_requires_a_generator_set_that_always_conforms` and
`test_smoke_execution_applies_constraints_and_traces_only_the_count`. Neither
failure imports or executes evaluation code. Exact final command counts are
recorded after the last verification run below.

Live acceptance used the configured DeepSeek service and the existing local
Phoenix 19 service. It never contacted a target API:

- Plan `plan-merge-duplicate-observations`: Experiment
  `RXhwZXJpbWVudDox`, trace `56c92c67e6f1a6015601e0004b7084f3`.
  All applicable scores were `1`; two undeclared scores were N/A.
- Solve `solve-memory-patch-apply`: the first Experiment exposed that the
  evaluation Adapter had omitted production's complete Generator snapshot.
  The Adapter was corrected and protected by a deterministic test. Final
  Experiment `RXhwZXJpbWVudDoz`, trace
  `6ca4933fff7dca204606a38b72b39ecc`, applied the scripted `3..100`
  Patch; all five applicable scores were `1` and one was N/A.
- Patch `patch-integer-range`: Experiment `RXhwZXJpbWVudDo0`, trace
  `7db72f0594c8cc0946dffbd2f83f963f`. The real Agent compiled the
  integer `3..100` Generator, generated five seeded samples, reviewed them,
  and accepted on output 2. All three applicable scores were `1`; two were
  N/A.

The live trace review also preserved a useful quality finding rather than
silently optimizing production behavior: Solve consumed 18 top-level LLM
outputs plus two scripted Patch outputs before applying the candidate. DeepSeek
repeatedly used malformed tool names, treated the terminal action as a tool,
and returned several incorrect terminal DTO shapes. This work deliberately
does not change the production prompt; the Experiment is now a reproducible
baseline for a separate prompt-optimization comparison.

The first live attempt also found that an environment HTTP proxy intercepted
loopback Phoenix traffic. The CLI now bypasses environment proxies only for
`localhost`, `127.0.0.1`, and `::1`; remote Phoenix endpoints keep normal proxy
behavior. A deterministic regression test protects that boundary.

Final commands:

- `uv run --group evaluation pytest -q
  tests/test_operation_smoke_evaluations.py tests/test_smoke_plan_agent.py
  tests/test_failure_solver_agent.py tests/test_parameter_patch_agent.py`:
  `29 passed`.
- `uv run pytest -q`: `480 passed, 4 skipped, 2` unchanged baseline failures.
- `uv run --extra tracing pytest -q`: `480 passed, 4 skipped, 2` unchanged
  baseline failures.
- `python -m compileall -q restscope evaluations tests`: passed.
- `git diff --check`: passed.
- Residual source scan confirmed that evaluation Modules do not import the
  RESTScope database package or a target HTTP client. The only HTTP client in
  the CLI is the official Phoenix client's loopback proxy-safe transport.
