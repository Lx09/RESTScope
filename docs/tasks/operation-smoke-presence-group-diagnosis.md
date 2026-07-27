# Operation Smoke Presence, Group Scope, and Diagnosis State

Status: Implemented and locally verified; uncommitted

## Objective

Fix three blockers confirmed by the latest Project API Phoenix trace:

- a nested input patched with `inclusion_probability=1` could still disappear
  because an object or body ancestor was omitted;
- a system-managed request-body container could be handed to the Parameter
  Patch Agent instead of its patchable leaf inputs;
- an active diagnosis continued accepting the initial-state hypothesis shape,
  which let wrappers, malformed candidates, and repeated hypotheses consume
  unnecessary model calls.

## Approved scope

- Give an explicit Generator Patch a deterministic presence closure from each
  mandatory descendant through every ancestor.
- Use the same closure in preview, local samples, provisional configuration,
  candidate staging, and accepted/rejected input derivation.
- Keep array presence separate from array cardinality.
- Mark system-managed semantic inputs in diagnosis prompts, reject them from
  ready/confirmed handoff, and defer them defensively during grouping.
- Split diagnosis into initial and active action protocols. Keep bounded
  run-local hypothesis history, compare normalized material signatures, and
  allow only one repair for an invalid active-state output.

## Non-goals

- Modifying the OpenAPI document, Provider interfaces, persistence boundaries,
  or public Operation Smoke request DTOs.
- Opening every descendant when a container is mistakenly grouped.
- Inferring leaf inputs from natural-language requirements.
- Adding a custom date Generator.
- Running a Project API live test.
- Committing, merging, or cleaning the feature worktree without separate
  authorization.

## Decisions

- Presence closure is a Patch semantic only. It does not change baseline
  generation for required children inside optional containers.
- An explicit ancestor probability below one conflicts with a mandatory
  descendant and raises `presence_closure_conflict`.
- Object, media-root body, and request-body control inputs remain valid
  diagnosis/probe concepts but are not Parameter Patch handoff inputs.
- Multiple corrected leaves enter one Group only when an explicit
  `interaction_notes` relationship connects them.
- Initial diagnosis accepts `ready`, `hypothesis`, or `deferred`. Active
  diagnosis accepts an HTTP probe, `confirmed`, `replace`, or `deferred`.
- A material hypothesis signature is order-insensitive for target inputs and
  proposed changes and is compared against all hypotheses in the current
  failure's run-local history.
- Repeating a historical hypothesis receives one active-state correction;
  repeating it again defers as `stalled_hypothesis`.

## Verification

Fresh local verification used the repository virtual environment directly:

- `pytest -q tests/test_testing_generation.py
  tests/test_testing_config_catalog.py`: 77 passed.
- `pytest -q tests/test_parameter_patch_agent.py`: 18 passed.
- `pytest -q tests/test_operation_smoke_plan_solve.py`: 36 passed.
- `pytest -q tests/test_operation_smoke_agent.py
  tests/test_supervisor_operation_smoke.py`: 17 passed.
- `pytest -q`: 513 passed, 4 skipped.
- `python -m compileall -q restscope tests`: passed.
- `git diff --check`: passed.

The full suite was rerun after the final protocol-example correction. No live
target, Provider, Project API, or Phoenix service was exercised by this task.
The implementation remains uncommitted pending separate Git authorization.
