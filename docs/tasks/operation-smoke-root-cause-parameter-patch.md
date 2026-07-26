# Operation Smoke Root-Cause and Parameter Patch Agent

Status: Implemented and offline-verified

## Objective

Separate evidence-backed HTTP root-cause investigation from
Generator/Constraint construction, then accept patches only when one real
same-seed candidate Smoke batch resolves associated initial failures.

## Approved decisions

- Investigate no more than ten deduplicated failures in FIFO order.
- Require either sufficient cited evidence for `ready` or a testable hypothesis
  before HTTP probing.
- Scope-check all HTTP calls in an output before executing any of them.
- Give each failure twenty valid outputs and three consecutive invalid repairs.
- Group only confirmed target inputs and immutable desired changes.
- Create one fresh, serial Parameter Patch Agent per Group.
- Generate exactly ten deterministic local samples and require same-Agent
  review before acceptance.
- Keep Generator, Constraint, solving, and generation semantics in
  `restscope.testing`.
- Execute all successful Groups in one normal candidate Smoke batch.
- Judge only initial failures, accept a Group when any associated initial
  failure resolves, and let the global threshold accept all successful Groups.
- Persist none of the investigation, Patch conversation, samples, or runtime
  Constraints.

## Implementation record

- Added `restscope.agent.parameter_patch` with Agent, factory, schemas, prompts,
  compilation, local sampling, and tracing.
- Added per-failure investigation, HTTP Probe preflight, probe failure queueing,
  provenance, immutable grouping, Group summaries, and effect validation to
  `restscope.agent.operation_smoke`.
- Added pure Generator preview and shared semantic input mapping to
  `restscope.testing`.
- Replaced the public Smoke budgets with
  `max_diagnosis_outputs_per_failure` and `max_patch_attempts`.
- Added distinct root-cause, grouping, Patch, and effect-validation model roles.
- Updated Agent package facades and compatibility re-exports.
- Unified Probe and Batch failure normalization so a reproduced HTTP failure
  retains the same signature and cannot be falsely confirmed.
- Enforced same-Group routing for interaction-linked inputs, latest-candidate
  acceptance in the Patch Agent, and interrupted candidate recovery.
- Changed Patch samples to request-shaped `values` plus explicit `present`
  evidence, including structural array values.
- Routed grouping- and Patch-failed items to deferred results and required
  effect validation to classify the complete initial failure set.

## Verification

Fresh offline verification:

- `tests/test_operation_smoke_plan_solve.py`: `18 passed`.
- `tests/test_parameter_patch_agent.py`: `12 passed`.
- Operation Smoke and Supervisor: `15 passed`.
- Phoenix tracing contract and Agent boundaries: `6 passed, 1 skipped`.
- Complete suite: `431 passed, 16 skipped`.
- Python compileall and `git diff --check`: passed.

No live Provider, target API, or Phoenix service was exercised. No commit,
merge, push, or worktree cleanup is claimed here.
