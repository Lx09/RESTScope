# RESTScope Database Design

Status: Active exploratory design (2026-08-01)

RESTScope creates one SQLite file for one App. The file is an audit artifact,
not a recovery image: a later App always rejects that existing path and never
deletes, migrates, overwrites, or resumes it. The current baseline contains 19
business tables plus Alembic's `alembic_version` table.

## Boundary

- `restscope.catalog` owns the database-independent current OpenAPI and change
  event contracts.
- `restscope.testing` owns the App-memory operation snapshot and current
  per-input Generator and Constraint contracts.
- API Behavior Monitor owns bounded Resource Identifier and Response Value
  evidence.
- Operation Smoke owns stable Failures and append-only terminal Solve Attempts.
- `restscope.db` owns SQLAlchemy mappings, repositories, transactions, foreign
  key setup, and the one baseline migration.
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

## Generator and Constraint: 3 tables

### `input_generator_configs`

One row per deterministic `input_node_id` stores the current inclusion
probability and Generator strategy. The operation's method, path, input tree,
media choice, disabled reasons, and request serialization snapshot stay in
memory and are rebuilt with these rows. There is no operation snapshot,
revision number, initialization marker, or full historical copy.

### `operation_constraints`

Each row is one current normalized executable Constraint. Its ID is derived
from `operation_key + normalized expression`; its owner list is derived from
the expression's referenced input node IDs. An expression with no owner or an
input outside the operation is rejected.

A complete Constraint Patch starts from its real new owners and replaces every
old Constraint whose owner overlaps directly or transitively. The expanded old
scope is used only to find rows to replace; it does not enlarge the new owner.
A Generator-only Patch leaves all Constraints unchanged. Candidate sampling
uses this final replacement set.

### `generator_change_events`

One append-only event is linked one-to-one with the successful Solve Attempt
that applied it. It records only deterministic Generator and Constraint
insert/update/delete changes with per-item before and after values. Samples are
run-local and are never stored. A candidate with no actual current-state change
is rejected before an Attempt or event is written.

Current Generator rows, Constraint replacement, the applied Solve Attempt, its
input links, and the change event commit in one transaction. Exact current
content provides optimistic locking; a stale candidate rolls back and becomes
a separate `conflict` Solve Attempt whose root cause and input links come from
that candidate.

## Resource Identifier: 6 tables

- `resources`: canonical and normalized resource identity.
- `resource_aliases`: normalized alias primary key linked to one resource.
- `operation_resource_rules`: latest classification for one operation/group,
  including selector, access mode, and classification source. Method, path,
  aliases, and observed flags are derived rather than copied.
- `resource_identifiers`: every distinct typed identifier with first and last
  seen timestamps. Resource identifiers have no capacity eviction.
- `resource_operation_usages`: composite identifier/rule key with only the
  latest observation time.
- `resource_monitor_errors`: latest error for one operation/group. A later
  successful classification deterministically deletes that latest error.

## Response Value: 5 tables

- `response_value_monitors`: natural `value_name` primary key and one unique
  consumer operation/input registration. Every stored monitor is active.
- `response_value_sources`: natural composite key for an explicit producer
  status/media/selector feeding one value pool.
- `response_values`: typed natural key plus first/last seen timestamps. Each
  pool retains its 100 most recently active distinct values.
- `response_observations`: successful JSON observation metadata. Each producer
  operation retains its latest 100 observations.
- `response_observation_scalars`: natural selector/type/value key under one
  observation. Deleting an old observation cascades to its scalars.

Flattening more than 1000 supported non-null scalars skips the whole response
and returns a structured warning. At or below the limit, the observation,
scalars, all matching pool updates, and both retention passes share one
transaction. A failure cannot leave partial observation or pool evidence.

## Operation Smoke: 3 tables

### `smoke_failures`

A stable Failure key hashes the operation, normalized sorted message set, and
the complete suspected-input state. `null` means the one-fingerprint path
skipped attribution, `[]` means operation-level, and a non-empty array means
exact input attribution. Repeated evidence reuses the row and updates occurrence
count, last-seen time, and last HTTP status.

### `smoke_solve_attempts`

Every terminal `applied_patch`, `no_patch`, or `conflict` conclusion appends a
row with one non-empty `reason`. Applied and runtime-conflict rows also store
the selected reviewed candidate's root cause; no-Patch rows leave root cause
null. Attempts are never overwritten and there is no permanent resolved flag.

### `smoke_solve_attempt_parameters`

A composite Attempt/input key stores candidate-derived cause attribution in
affected-input order. Applied and runtime-conflict Attempts retain these links;
no-Patch has none and therefore does not appear in Parameter-specific history.
Input links reference current operation input rows; the deterministic
repository rejects unknown or cross-operation attribution.

Failure Dedup uses only the current run's in-memory Test Case Catalog and
persists messages, attribution state, and occurrence metadata—not the
representative case. Solve reads Failure and Parameter projections from this
memory, while HTTP probes and candidate samples stay temporary.

## Lifecycle and compatibility

The default App accepts only a nonexistent local file SQLite target. It claims
that exact path before migration. Existing files, directories, and symlinks are
rejected unchanged. Construction failure removes only a file and sidecars
created by that construction; successful construction, initialization failure,
and `close()` retain the artifact.

Alembic has one `0001_current_baseline` that creates the final 19 tables. It
contains no old-database data migration. Databases stamped with the retired
exploratory chain are intentionally incompatible, and RESTScope provides no
restore, reset, or automatic delete entrypoint.

## Deferred design

- Reopening, restoring, or comparing App artifacts.
- Multi-API namespaces and long-term schema history.
- Persisted plans, inferred operation graphs, queues, or progress UI.
- Authentication material or target API path bindings.
