---
status: accepted
---

# Atomically replace Generation State and response-value pools

## Decision

Request Generation owns Parameter Patch application through one deep
`RequestGenerationPatchRuntime`. Its public operations are state read,
validation, and application. Tool Modules translate those operations to the
existing model contracts; the Harness binds the App-constructed runtime and
does not assemble or inspect Patch domain state.

A Response Value Source is complete replacement state. Changing a producer
field replaces the pool's entire source set and rebuilds its typed values from
retained observations. Changing a Generator away from `response_value` removes
that input's pool. Constraint-only participation leaves its pool unchanged.
Exact source bindings are part of the in-memory Generation State digest, so a
source-only change advances the revision.

Apply holds the Operation lock while it revalidates the Patch, stages durable
pool replacements, publishes the new in-memory state, and commits the database
transaction. If the durable commit fails after publication, the runtime
restores the previous in-memory state before releasing the lock. An exact
no-op is rejected before opening the durable write transaction.

A Batch acquires the same Operation lock while it freezes the complete
Generation State and all reference pools named by that state. It releases the
lock before generation and HTTP execution, then uses only that immutable pool
snapshot for every case.

## Consequences

- Readers cannot observe a new Generator with an old pool, or an old Generator
  with a committed replacement pool, within one running App.
- Multiple concurrent Apply calls using one old revision still have only one
  winner.
- Response pools remain bounded materialized evidence; they are not redundant
  Patch history.
- No persistent Patch, rollback, candidate, sample, or Agent state is added.
- Restart still discards Generation State and rebuilds defaults from OpenAPI.

This sharpens [ADR 0003](0003-retire-operation-smoke-and-apply-parameter-patches.md),
which established the Tool and Skill workflow but did not fully define the
cross-owner transaction or source-replacement semantics.
