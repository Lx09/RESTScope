# Retire Operation Smoke and Apply Parameter Patches

Status: Implemented and verified in `codex/retire-operation-smoke`

## Objective

Remove the specialized Operation Smoke architecture and replace its reusable
mechanical capabilities with generic Tools plus standard Skills. Keep the Main
Profile plan-only so capability construction does not silently grant live API
testing or state mutation.

## Implemented scope

- Removed `restscope.operation_smoke`, specialized Failure/Patch/Review/Compact
  Agents, candidates, Finalizer, persistent Smoke/Generator Models, old
  Test Case/Worklist/Parameter Tools, current-operation HTTP Probe, and the
  `evaluations` package.
- Reduced the baseline database from 19 to 13 business tables.
- Added the App-lifetime `RequestGenerationConfigStore` with per-operation
  revision, digest, lock, complete Generator/Constraint state, and frozen
  snapshots.
- Added `openapi.list_operations`, `request_generation.get_input_state`,
  `request_generation.validate_patch`, `parameter_patch.apply`, and
  `test_case.run_batch` with production Harness bindings.
- Added deterministic semantic Patch validation, reference checks, finite-domain
  solving, bounded witnesses, validation digests, atomic response-source
  registration, and conflict-safe Store replacement.
- Replaced `build-parameter-patch` with `apply-parameter-patch` and expanded its
  method through state confirmation. Reworked `resolve-operation-failures` for
  inline Batch evidence and generic child delegation.
- Removed the Observer's special Smoke event and advanced browser history to
  schema-v3, where Batch and Apply are ordinary Tool cards.

## Safety boundaries

- Store state, Patches, samples, Failures, and Agent work remain non-persistent.
- Apply performs no HTTP call and provides no automatic rollback.
- A validation or Apply error leaves Store state unchanged.
- A Batch freezes one state revision before generating any case.
- Tool binding is not authorization; the production Main Profile still grants
  only `plan.read` and `plan.update`.
- No real model, target API, MCP, Phoenix, or other external service is used by
  verification.

## Verification contract

The change is complete only when focused Tool/Skill/Profile/database/Observer
tests, the full Python suite, `compileall`, standard Skill validation, wheel
content inspection, UI tests/lint/build, and `git diff --check` all pass from
the feature worktree. Git commit, merge, cleanup, and push remain separately
authorized operations.
