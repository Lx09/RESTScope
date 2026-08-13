# RESTScope Database Design

Status: Active exploratory design (2026-08-13)

RESTScope creates one SQLite file for one App. The file is an evidence and audit
artifact, not a recovery image: a later App rejects the existing path and never
resumes it. The fresh baseline contains eleven business tables plus Alembic's
`alembic_version` table.

## Boundary

- `restscope.api_behavior_monitor.catalog` is the single database-independent
  Interface for the current normalized OpenAPI, append-only contract changes,
  operations, batches, observations, resources, input sources, abstract test
  cases, and final Bug Oracle assessments.
- `restscope.request_generation` keeps the current revisioned
  Generator/Constraint state in memory. Exact source bindings participate in
  that state, but producer values are parsed from observations on demand.
- `restscope.db` owns one API Behavior SQLAlchemy Adapter, one ORM module,
  foreign-key setup, and the one fresh baseline migration. The concrete
  Repository stays private; only the Unit of Work is an infrastructure entry.
- LLM reasoning, extraction rules, Patch history and samples, Failures, plans,
  queues, scheduler state, and restorable Agent state never enter SQLite.

Every SQLite connection enables foreign keys. Fresh database creation finishes
with integrity and foreign-key checks. Request headers conventionally carrying
authorization, cookies, tokens, API keys, or secrets are removed before an
observation is written.

## API Behavior Catalog: 11 tables

All eleven tables belong to one Catalog and one SQLAlchemy transaction family.
The headings below group their contents for explanation; they are not separate
runtime repositories or App collaborators.

### OpenAPI audit facts: 2 tables

#### `openapi_current`

The singleton row stores the mutable current normalized OpenAPI document used by
Contract Monitor. A real observed response-contract change replaces the document
atomically with its change event. Bug Oracle does not read OpenAPI.

#### `openapi_change_events`

Each real response-contract change records operation text, actual status,
normalized media type, change labels, and affected Response before/after data.
Contract matches, pending retries, and internal check failures add no event.

### Response and resource evidence: 8 tables

#### `operations`

`operation_id` is the normalized `METHOD /path` primary key. `method + path` is
also unique; `description` is refreshed from the current OpenAPI IR.

#### `resources`

`resource_id` is the database identity. `name` is the unique lowercase
alphanumeric value formerly called `normalized_name`. `identity_fields` is an
immutable sorted list of direct response properties. Each instance must contain
every field as a string or non-Boolean integer.

#### `operation_resource_edges`

The primary key is `(operation_id, resource_id, role)`. Roles describe how the
response uses the resource: `CREATED`, `REFERENCED`, `UPDATED`, or `DELETED`.
`_alpha` and `_beta` store the neutral Beta(1,1) proposition evidence. This
version defines no evidence-update policy or Tool.

#### `resource_instances`

The primary key is `(resource_type, resource_instance_id)`, where
`resource_type` stores `resources.name` and the instance ID is canonical typed
JSON over all identity fields. `current_state_json` is updated incrementally:
missing properties stay, nested objects merge recursively, arrays replace as a
whole, and a new null never overwrites old state. `_deleted=true` is logical
deletion and deleted instances are hidden from ordinary generation and Tools.

#### `observations`

Every matched sent request that reaches an HTTP response or transport failure
is permanent. `observation_id` is also its Test Case ID. Each row stores the
operation, completion time, sanitized actual request envelope, explicit
`outcome_kind`, and optional Abstract Test Case/Batch identity. HTTP rows store
status 100–599, reason phrase, media type, complete response headers, exact body
bytes, and their JSON/text/Base64 presentation kind. Transport rows instead
store a stable failure code/message with no HTTP status. `batch_id` and the
zero-based `batch_case_index` appear together and are unique as a pair. A Replay
instead has one unique `replay_of_observation_id`, no Batch fields, and the same
operation as its Primary Observation.

#### `oracle_assessments`

One immutable row belongs to one Primary HTTP Observation and may reference its
single Replay. Assessment schema version 2 stores the derived Boolean Bug verdict
and one strict `unexpected_response_status` Check. Primary and Replay each retain
their canonical trigger reason set; only exact `reproduced` equality is a Bug.

Persistence has no retention deletion or per-response body limit. Learning
queries independently select no more than the latest 100 complete valid 2xx
JSON Observations per operation. Response-value reuse, observed-field discovery,
and resource extraction cannot consume other rows.

#### `operation_input_sources`

The composite primary key records consumer operation/input, producer
operation, concrete successful status, normalized media type, selector,
display field name, and `consume_type` (`RESOURCE` or `VALUE_REUSE`). `_alpha`
and `_beta` start at Beta(1,1) and are not updated in this version. Two consume
types may coexist for identical response coordinates.

No response-value table exists. A VALUE_REUSE Generator selects typed scalars
from matching eligible Observations when it needs values. A RESOURCE Generator
reads current non-deleted instances and uses one shared per-case resource seed,
so composite identity components never form an unobserved combination.

#### `abstract_test_cases`

One immutable row per `(operation_id, state_digest)` stores the complete
Generator configuration, exact reference bindings, and Constraints. Batch
preflight writes or reuses it after every request is generated and serialized
but before the first network call. It is audit metadata, not a restorable
Generation Store or per-concrete-case registry.

#### `batches`

`batch_id` is the durable execution identity. `summary` is complete bounded JSON
covering running/completed/failed status, operation and generation identity,
Abstract Test Case, seed, requested/executed/persisted counts, HTTP status
distribution, transport failure count, and safe persistence logs. A Batch is
created after all cases preflight and the Abstract Test Case commits, but before
the first network call. It is evidence, not a resumable queue or scheduler.

## Response processing order

For every matched response, Observation commits its complete factual result
first. One decoded response evidence value then feeds current Contract Monitor
and Resource Monitor. Bug Oracle reads only status and Generator validity after
those stages. A candidate triggers one identical-request Replay through the same
processing path and becomes a Bug only when its complete reason set repeats.
Matched transport failures commit without a Contract check, and Replay failures
finalize the Check without replacing the Primary target result.

Ordinary HTTP Tool calls use no Batch fields. Batch execution creates one
running summary before its first send, updates progress best-effort, and ends as
completed even when cases include non-2xx or transport failures. Unexpected
execution defects mark it failed and preserve earlier Observations. A missing
Observation or summary update produces a bounded warning without suppressing
later requests or already available inline results.

Unknown resource groups ask the bounded Resource Identifier System Agent for
direct identity fields. Existing unambiguous definitions are reused. No model
reasoning or extraction rule is persisted.

## Lifecycle and compatibility

The default App accepts only a nonexistent local SQLite file, claims it before
migration, and retains it after close. Existing files, directories, and
symlinks are rejected unchanged. Alembic has one `0001_current_baseline` and no
old-database data migration. Retired Resource Catalog and Response Value pool
schemas are intentionally incompatible with this fresh baseline.

## Deferred design

- Evidence update semantics, confidence-based selection, merging, or decay.
- Database encryption, secure deletion, automatic vacuuming, and artifact
  reopening or restoration.
- Persisted extraction rules, plans, operation graphs, scheduler queues, or
  Agent memory.
