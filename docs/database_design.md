# RESTScope Database Design

Status: Active exploratory design (2026-08-10)

RESTScope creates one SQLite file for one App. The file is an audit artifact,
not a recovery image: a later App always rejects that existing path and never
deletes, migrates, overwrites, or resumes it. The current baseline contains 14
business tables plus Alembic's `alembic_version` table.

## Boundary

- `restscope.openapi_audit` owns the database-independent current OpenAPI and
  change-event contracts.
- `restscope.request_generation` owns the App-memory operation snapshot and
  revisioned current Generator/Constraint state. It has no database Adapter.
- `restscope.harness.operation_testing` owns deterministic Batch execution and
  run-local Test Case evidence; neither is persisted.
- API Behavior Pool owns bounded Resource Identifier and Response Value
  evidence.
- `restscope.db` owns SQLAlchemy mappings, domain-adjacent persistence Adapters,
  transactions, foreign-key setup, and the one baseline migration.
- Raw responses, Test Cases, Batches, model messages, Patch samples, plans,
  queues, and scheduler state never enter the database.

Every SQLAlchemy and Alembic SQLite connection executes
`PRAGMA foreign_keys=ON`. Fresh database creation finishes with
`PRAGMA integrity_check` and `PRAGMA foreign_key_check`.

## OpenAPI: 2 tables

### `openapi_current`

One row with primary key `singleton_id=1` stores the complete normalized
OpenAPI 3.1 document. `RESTScopeApp.initialize()` inserts it after parsing the
source. A real observed response-contract change replaces the whole document.
`created_at` and `updated_at` record those boundaries.

### `openapi_change_events`

Each real response-contract change appends one event containing the operation
key, status, normalized media type, change labels, and the affected Response
before and after the change. Matched, pending, and failed checks add no event.
The tracker holds one lock while it backs up the affected in-memory Response,
changes the IR, builds the complete document, and commits current state plus the
event. A database failure restores both IR and tracker retry state.

The App exposes read-only current-document export and operation-filtered event
listing. Neither API restores an App.

## Generator and Constraint: no tables

`RequestGenerationConfigStore` initializes one revision-0 state for every
OpenAPI operation and keeps it only for the current App lifetime. A Batch
freezes one complete revision and every reference pool named by that revision.
A validated Parameter Patch replaces Generator, Constraint, and exact
reference-binding state under the operation lock and increments the revision.
Restarting the App recreates defaults from OpenAPI.

The Store deliberately has no Patch history, candidate registry, rollback
record, Failure memory, sample storage, or database mapping. Response-value
pool sources used by a Patch remain API Behavior Pool evidence. Apply stages
their durable replacement, publishes in-memory state, then commits; a commit
failure restores the old state before unlocking.

## Resource Identifier: 7 tables

- `resources`: canonical and normalized resource identity.
- `resource_aliases`: normalized alias primary key linked to one resource.
- `resource_identifier_definitions`: one stable identifier name per resource
  with its ordered component names. A Definition may have one component or a
  path-ordered combination.
- `operation_resource_rules`: latest classification for one operation/group,
  including the referenced definition, selected full path, ordered response
  field mappings, access mode, and classification source. Method, operation
  path, aliases, and observed flags are derived rather than copied.
- `resource_identifiers`: every distinct complete typed Identifier Record as
  ordered JSON plus a type-sensitive digest and first/last seen timestamps.
  Resource identifiers have no capacity eviction.
- `resource_operation_usages`: composite identifier/rule key with only the
  latest observation time.
- `resource_monitor_errors`: latest error for one operation/group. A later
  successful classification deterministically deletes that latest error.

## Response Value: 5 tables

- `response_value_pools`: natural `value_name` primary key and one unique
  consumer operation/input pool. Every stored pool is active.
- `response_value_pool_sources`: natural composite key for the complete
  producer status/media/selector set feeding one value pool. Patch replaces
  this set instead of appending an implicit alternative.
- `response_value_pool_values`: typed natural key plus first/last seen timestamps. Each
  pool retains its 100 most recently active distinct values.
- `response_observations`: successful JSON observation metadata. Each producer
  operation retains its latest 100 observations.
- `response_observation_scalars`: natural selector/type/value key under one
  observation. Deleting an old observation cascades to its scalars.

Flattening more than 1000 supported non-null scalars skips the whole response
and returns a structured warning. At or below the limit, the observation,
scalars, all matching pool updates, and both retention passes share one
transaction. A failure cannot leave partial observation or pool evidence.

## Lifecycle and compatibility

The default App accepts only a nonexistent local file SQLite target. It claims
that exact path before migration. Existing files, directories, and symlinks are
rejected unchanged. Construction failure removes only a file and sidecars
created by that construction; successful construction, initialization failure,
and `close()` retain the artifact.

Alembic has one `0001_current_baseline` that creates the final 14 tables. It
contains no old-database data migration. Databases stamped with the retired
exploratory chain are intentionally incompatible, and RESTScope provides no
restore, reset, or automatic delete entrypoint.

## Deferred design

- Reopening, restoring, or comparing App artifacts.
- Multi-API namespaces and long-term schema history.
- Persisted plans, inferred operation graphs, queues, or progress UI.
- Authentication material or target API path bindings.
