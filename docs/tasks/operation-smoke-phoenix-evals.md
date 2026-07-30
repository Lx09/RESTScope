# Operation Smoke Phoenix Evals

Status: Implemented, verified, and merged into local `main`

Subsequent note: `docs/tasks/project-agent-context.md` replaces the original
model-facing prompt projections and Planner memory-tool assumption. The
Phoenix Dataset/Experiment structure remains active.

Current follow-up status: Agent Context Dataset resynchronization completed.

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

## 2026-07-30 Agent Context resynchronization

The user approved replacing the live localhost Phoenix Dataset contents with
the current repository Scenarios and running the configured DeepSeek model for
all three Agents. No target API call is authorized or required.

Before synchronization, the three Planner examples still stored the retired
`memory_failure_ids` reference field even though their stable Scenario IDs
matched the repository. Current Planner Scenarios use
`candidate_failure_ids`; Solve and Patch were already current.

`sync_suite` now treats the Dataset object returned by Phoenix as evidence. It
compares stable IDs and the complete model-facing input, reference output, and
metadata with the uploaded repository examples. Missing, unexpected,
duplicate, or stale examples abort before an Experiment starts. Split upload
remains protected by the existing deterministic call-contract test because
Phoenix does not return per-example split labels in the Dataset response.

Live synchronization produced Planner Dataset version
`RGF0YXNldFZlcnNpb246NA==`; Solve and Patch remained on versions 2 and 3
because their content was unchanged. A fresh read confirmed exact IDs and
content for all nine examples.

The first all-scenario Agent experiments then showed that Solve Experiment
`RXhwZXJpbWVudDo2` and Patch Experiment `RXhwZXJpbWVudDo3` passed every
applicable evaluator without task errors. Plan Experiment
`RXhwZXJpbWVudDo1` exposed two current semantic problems:

- The prompt did not say that generated-value rejection, target validation,
  response mismatch, missing-resource, and transport evidence remains
  debuggable, so DeepSeek incorrectly returned `no_debug` for the independent
  namespace and date failures.
- The old `plan-reuse-history-nondebuggable` expected a connection reset to be
  non-debuggable, even though current Solve can safely perform a bounded
  current-operation HTTP probe.

A fresh read from localhost Phoenix confirmed the exact evidence rather than
relying on the earlier console summary: Plan had 3 tasks, 0 task/evaluator
errors, and 10 of 14 applicable scores at `1`; Solve had 3 tasks, no errors,
and all 15 applicable scores at `1`; Patch had 3 tasks, no errors, and all 11
applicable scores at `1`. The opaque IDs above represent Experiments 5, 6, and
7 respectively.

The prompt now states that boundary directly. The old Scenario was replaced by
`plan-reuse-history-and-transport`, which expects one reused known Failure and
one separate transport Todo. Plan synchronization created Dataset version
`RGF0YXNldFZlcnNpb246NQ==`; the exact mirror check proved that the retired ID
was deleted and all current fields matched.

Current local verification:

- Evals plus Plan, Solve, and Patch Agent tests: `34 passed`.
- Full repository suite: `492 passed, 4 skipped, 2 failed`.
- The two failures reproduced unchanged on local `main` in
  `test_object_cardinality_requires_a_generator_set_that_always_conforms` and
  `test_smoke_execution_applies_constraints_and_traces_only_the_count`.
- `python -m compileall -q restscope evaluations tests`, `git diff --check`,
  and a retired-Scenario-ID source scan passed.

After the user explicitly authorized the three sanitized payloads, Plan
Experiment `RXhwZXJpbWVudDo4` proved the semantic correction: all 3 tasks
completed without task or evaluator errors, and all 13 applicable scores were
`1`. A pre-commit trace review nevertheless found that all three tasks first
returned invalid `debug`/`debuggable` enum values and relied on correction
rounds. The production prompt and regression test were therefore deepened to
state the exact action/disposition values, the conditional
`disposition_reason` rule, and the required non-null top-level `reason`.

Final Plan Experiment `RXhwZXJpbWVudDoxMA==` ran against Dataset version
`RGF0YXNldFZlcnNpb246NQ==`. Each of its three tasks produced a valid Plan in
one DeepSeek response with no correction round, no task error, and no evaluator
error. Its 13 applicable scores were all `1`, with 5 undeclared properties
reported as N/A:

- candidate retrieval: `3/3`;
- independent/equivalent case grouping: `3/3`;
- Plan status: `3/3`;
- runtime health: `3/3`;
- historical Failure reuse: `1/1` applicable.

This closes both the first Plan Experiment's two semantic findings and the
later format-efficiency finding. Together with Solve's 15/15 and Patch's 11/11
applicable scores, all three current production Agents now pass their complete
three-Scenario live suites.

During Git preparation, the first targeted restore of the task's temporary
root planning files was denied because the sandbox could not create the
worktree index lock. The same exact three-file restore succeeded with the
already authorized Git access. The main worktree's existing planning files and
untracked live test were not changed.
