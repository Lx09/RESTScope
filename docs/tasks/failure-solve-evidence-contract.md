# Failure Solve Evidence Contract

## Status

Implemented and freshly verified for authorized local delivery from the
dedicated `codex/failure-solve-evidence-contract` branch. No live call or push
is part of this work.

## Objective

Allow Failure Solve to request a Parameter Patch only when observed evidence
supports both the affected inputs and their required values. Make the Patch
task distinguish a causal diagnosis, the required value domain, and the
independent value predicates used to review the result.

## Approved domain language

- `root_cause` is an evidence-backed causal conclusion explaining why the
  current value or relationship of a concrete input produces the target
  Failure Message. It does not describe the repair.
- `value_requirements` describes the types, allowed values, boundaries,
  formats, presence rules, or cross-input relationships the affected inputs
  need.
- `acceptance_criteria` is a non-empty list of independently checkable value
  predicates. Each predicate states the relevant type, allowed set, boundary,
  format, presence condition, or input relationship; it does not state an HTTP
  outcome.

## Evidence paths

Failure Solve may request a Parameter Patch through either of two paths:

1. An exact Failure Message explicitly states the applicable value rule.
2. A controlled HTTP Probe starts from the failed request, changes only the
   proposed affected inputs, preserves every other known input, and causes the
   target Failure Message to appear, disappear, or change as predicted.

Before either path, Solve reads the representative Test Case's exact Failure
Messages and the exact current value of every proposed affected input. OpenAPI
Schema, description, and example data may form a hypothesis or supply a Probe
value, but do not independently prove causation. When neither evidence path is
satisfied, Solve continues investigating or returns `no_patch`.

## Approved scope

- Replace `desired_behavior` with `value_requirements` throughout the public
  Solve tool, Parameter Patch task, prompts, Review handoff, tests, and
  evaluation fixtures.
- Change `acceptance_criteria` from one string to a non-empty list of atomic
  value predicates, without a compatibility alias.
- Require Parameter Patch Review to report the exact unmet criterion after
  checking final Generators, Constraints, and samples.
- Add evaluation coverage for direct Failure Message evidence, controlled
  Probe evidence, and OpenAPI-only evidence that must not request a Patch.
- Continue writing `value_requirements` into the existing candidate
  `change_reason` field when a reviewed Patch is selected.

## Non-goals

- No runtime evidence gate, evidence DTO, persistence change, database change,
  or historical-record migration.
- No Testing Module, HTTP transport, provider, or live-target behavior change.
- No GitLab, DeepSeek, or Phoenix live calls and no push.

## TDD and verification

- RED/GREEN: public Patch tool Schema and Failure Solve evidence instructions.
  RED exposed the old `desired_behavior` field and scalar criteria.
- RED/GREEN: `ParameterPatchTask` field and list validation contract.
  RED rejected the new fields because the old DTO still owned the contract.
- RED/GREEN: evaluation maximum-call and Probe-before-Patch properties.
  RED showed that the evaluators could neither forbid a Patch call nor check
  the evidence-gathering order.
- `uv run --group evaluation pytest -q tests/test_failure_solver_agent.py
  tests/test_parameter_patch_agent.py tests/test_operation_smoke_evaluations.py`:
  99 passed.
- `uv run --group evaluation pytest -q
  tests/test_operation_smoke_evaluations.py`: 13 passed.
- `uv run pytest -q tests/test_llm_deepseek.py
  tests/test_workflow_package_boundaries.py`: 41 passed.
- `uv run --group evaluation pytest -q`: 673 passed and 5 skipped.
- `uv run python -m compileall -q restscope tests evaluations`: passed.
- `git diff --check`: passed.
- A scoped credential-pattern review found only the evaluation test that
  asserts serialized scenarios do not contain credential field names.
