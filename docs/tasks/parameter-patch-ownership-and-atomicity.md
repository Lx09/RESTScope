# Parameter Patch ownership and atomicity

Status: implemented; awaiting Git delivery authorization.

## Approved outcome

- Request Generation owns one deep Parameter Patch runtime with `read_state`,
  `validate`, and `apply` operations.
- The App composes that runtime. Harness and Tool Modules receive it as one
  capability and cannot reach its Store directly.
- Exact response producer bindings participate in Generation State identity.
- Response-value pool sources use complete replacement semantics.
- Durable pool replacement and in-memory revision publication are atomic for
  the running App, including rollback after a database commit failure.
- Batch execution freezes reference pools together with its Generation State.
- Database table and ORM names use pool terminology, while the total remains
  thirteen business tables.

## Deliberate boundaries

- `parameter_patch.apply` is still the only model-callable generation-state
  mutation.
- Tool input names and Skill names are unchanged.
- The initial production Main Profile remains Plan-only.
- There is no persistent Patch history, rollback record, candidate registry,
  sample store, Failure memory, or compatibility alias.
- Atomicity covers RESTScope's in-process Generation State and its response
  pool transaction. It does not cover a target API or survive process failure.

## Verification contract

Tests cover source-only revision changes, exact pool replacement, rollback of
published in-memory state after commit failure, concurrent stale revisions,
no-op rejection, and one-snapshot Batch reference use. Schema tests retain the
thirteen-table boundary and reject the retired response table names.
