# Redundant Protocol and Reference Integration Cleanup

Status: Complete

## Objective

Remove hypothetical single-implementation Protocols while retaining every
real Adapter seam. Give Parameter Patch one concrete reference collaborator
without changing its database/in-memory publication transaction.

## Approved decisions

- Rename `BehaviorMonitorReferenceValues` to `BehaviorMonitorReferences` with
  no compatibility alias.
- Replace `reference_values` plus `reference_binding_stager` with one optional
  `references` constructor argument on `RequestGenerationPatchRuntime`.
- Remove the Coordinator's duplicate Resource Tracker Protocol and App's
  private UI Host Protocol; use their concrete classes.
- Keep all database, Agent, Tool, third-party, response-processing, System
  Agent, and multi-implementation read Protocols listed in the approved plan.

## Non-goals

- No database or Tool Schema change, persistence change, response-flow change,
  or global ports reorganization.
- No compatibility alias, new Protocol, Adapter, or capability probe.
- No Git staging, commit, or push.

## Verification

- Concrete reference validation, staging, publication, commit, and rollback.
- Ordinary and reference-backed Patch behavior with and without references.
- Resource response stages, UI lifecycle, Batch value freezing, facade and
  package navigation.
- Complete tests, `typing.Any` guard, compilation, retained-Protocol audit,
  and `git diff --check`.

Fresh results on 2026-08-12:

- Parameter Patch, Batch, Monitor, App/UI, and package integration suite:
  66 passed.
- `uv run pytest -q`: 565 passed, 2 skipped.
- `typing.Any` guard, Python compilation, obsolete-name scan, retained-Protocol
  inventory, exact Context Manager type inspection, and `git diff --check`
  passed.
